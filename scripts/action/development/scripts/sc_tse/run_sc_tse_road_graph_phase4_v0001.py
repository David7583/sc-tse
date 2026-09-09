# ============================================================
# 文件名: run_sc_tse_road_graph_phase4_v0001.py
# 中文名: RoadGraph Phase 4 工程适配入口
# 版本号: v0001
#
# 主层级: action
# 层级: development / sc_tse / phase4_road_graph_adapter
# 脚本定位: 将统一 RoadGraph 逐走廊交给现有 Phase 4 工程校核
#
# 职责说明:
# - 保留 RoadGraph 拓扑并复用既有单路线 Road Alignment、水平和竖向 Provider
#
# 本脚本做什么:
# - 简化冗余 A* 顶点、构造显式 route、汇总走廊与全网硬约束状态
#
# 本脚本不做什么:
# - 不生成 RoadGraph，不移动建筑，不修改 Phase 4 Core，不控制工作流终态
#
# 制度边界声明:
# - 输入只读；输出保留完整 source_refs；任一走廊失败即全网失败
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: run_sc_tse_road_graph_phase4_v0001
# family: run_sc_tse_road_graph_phase4
# role: phase4_road_graph_adapter
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/sc_tse/run_sc_tse_road_graph_phase4_v0001.py
# input:
#   - admitted RoadGraphRun, RoadProblem template and Phase 4 config
# output:
#   - RoadGraphEngineeringRun with per-corridor RoadRun evidence
# depends_on:
#   - run_sc_tse_phase4_v0001
#   - Shapely 2.x
# used_by:
#   - run_sc_tse_orchestrator_v0004
#   - test_sc_tse_basic_road_network_v0001
# ============================================================

from __future__ import annotations

import copy
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Mapping

import jsonschema
from shapely.geometry import LineString

from run_sc_tse_phase4_v0001 import run_phase4

DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "run_sc_tse_road_graph_phase4"
SCRIPT_NAME = "run_sc_tse_road_graph_phase4_v0001"
SCRIPT_VERSION = "v0001"
RESULT_VERSION = "sc-tse-road-graph-engineering-result-v0.1.0"


# ============================================================
# 异常类型
# ============================================================

class RoadGraphEngineeringError(RuntimeError):
    """Raised when a RoadGraph cannot enter Phase 4 engineering."""


# ============================================================
# 工具函数区
# ============================================================

def _hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode(DEFAULT_ENCODING)).hexdigest()


def _schema(filename: str) -> dict[str, Any]:
    local = Path(__file__).resolve().parent / "schemas" / filename
    active = next(
        parent / "scripts/action/development/scripts/sc_tse/schemas" / filename
        for parent in Path(__file__).resolve().parents
        if (parent / "PROGRAMMING_LANGUAGE_SCRIPT_FORMAT.md").is_file()
    )
    return json.loads((local if local.is_file() else active).read_text(encoding=DEFAULT_ENCODING))


def _validate(value: Mapping[str, Any], filename: str) -> None:
    jsonschema.Draft202012Validator(_schema(filename)).validate(dict(value))


def _route_problem(
    template: Mapping[str, Any],
    polyline: Mapping[str, Any],
    adapter_config: Mapping[str, Any],
) -> dict[str, Any]:
    problem = copy.deepcopy(dict(template))
    simplified = LineString(polyline["coordinates"]).simplify(
        float(adapter_config["waypoint_simplification_m"]),
        preserve_topology=True,
    )
    waypoints = [[round(float(x), 9), round(float(y), 9)] for x, y in simplified.coords]
    closed = bool(polyline.get("closed"))
    if closed and waypoints[0] == waypoints[-1]:
        waypoints.pop()
    problem["requirements"] = {"closed_loop": False, "enclosed_block_ids": [], "connection_points": []}
    problem["route"] = {
        "waypoints": waypoints,
        "closed": closed,
        "width_m": float(adapter_config["width_m"]),
        "centerline_radius_m": float(adapter_config["centerline_radius_m"]),
    }
    problem["metadata"] = {
        **problem.get("metadata", {}),
        "route_source": "RoadGraphRun.road_graph.polylines",
        "source_polyline_id": polyline["polyline_id"],
    }
    return problem


# ============================================================
# 核心类
# ============================================================

def run_road_graph_phase4(
    road_problem_template: dict[str, Any],
    road_graph_run: dict[str, Any],
    phase4_config: dict[str, Any],
    adapter_config: dict[str, Any],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    _validate(road_graph_run, "sc_tse_road_graph_result_v0001.schema.json")
    _validate(adapter_config, "sc_tse_road_graph_phase4_config_v0001.schema.json")
    if road_graph_run.get("solve_status") != "FEASIBLE" or road_graph_run.get("hard_constraint_status") != "SATISFIED":
        raise RoadGraphEngineeringError("Phase 4 requires an admitted feasible RoadGraphRun")
    if road_problem_template["problem_id"] != road_graph_run["problem_id"]:
        raise RoadGraphEngineeringError("problem identity differs between RoadGraph and RoadProblem")
    if dry_run:
        return {"schema_version": RESULT_VERSION, "record_type": "RoadGraphEngineeringDryRun", "status": "dry_run", "problem_id": road_graph_run["problem_id"]}
    corridor_runs = []
    failures = []
    for polyline in road_graph_run["road_graph"]["polylines"]:
        problem = _route_problem(road_problem_template, polyline, adapter_config)
        road_run = run_phase4(problem, phase4_config)
        admitted = (
            road_run.get("execution_status") == "COMPLETED"
            and road_run.get("solve_status") == "FEASIBLE"
            and road_run.get("candidate", {}).get("hard_constraint_status") == "SATISFIED"
        )
        corridor_runs.append({
            "polyline_id": polyline["polyline_id"],
            "source_connection_ids": polyline["source_connection_ids"],
            "admission_status": "ADMITTED" if admitted else "REJECTED",
            "road_run": road_run,
        })
        if not admitted:
            failures.append({"polyline_id": polyline["polyline_id"], "reason": "PHASE4_CORRIDOR_NOT_FEASIBLE"})
    graph = road_graph_run["road_graph"]
    topology_satisfied = graph["connected_component_count"] == 1 and graph["cycle_count"] >= 1
    if not topology_satisfied:
        failures.append({"polyline_id": None, "reason": "ROAD_GRAPH_TOPOLOGY_NOT_SATISFIED"})
    feasible = not failures
    result = {
        "schema_version": RESULT_VERSION,
        "record_type": "RoadGraphEngineeringRun",
        "execution_status": "COMPLETED",
        "solve_status": "FEASIBLE" if feasible else "UNRESOLVED",
        "hard_constraint_status": "SATISFIED" if feasible else "VIOLATED",
        "problem_id": road_graph_run["problem_id"],
        "road_graph_id": road_graph_run["road_graph_id"],
        "road_graph_topology": {
            "connected_component_count": graph["connected_component_count"],
            "cycle_count": graph["cycle_count"],
            "status": "SATISFIED" if topology_satisfied else "VIOLATED",
        },
        "candidate": {
            "candidate_id": "ROAD-GRAPH-ENGINEERED-" + _hash([road_graph_run["road_graph_id"], corridor_runs])[:20],
            "hard_constraint_status": "SATISFIED" if feasible else "VIOLATED",
        },
        "corridor_runs": corridor_runs,
        "failure_data": failures,
        "source_refs": {
            "road_graph_result_hash": _hash(road_graph_run),
            "road_problem_template_hash": _hash(road_problem_template),
            "phase4_config_hash": _hash(phase4_config),
            "adapter_config_hash": _hash(adapter_config),
            "phase4_provider": "run_sc_tse_phase4_v0001",
        },
        "observability": {"total_ms": (time.perf_counter() - started) * 1000.0, "corridor_count": len(corridor_runs)},
    }
    _validate(result, "sc_tse_road_graph_engineering_result_v0001.schema.json")
    return result


# ============================================================
# CLI / main 接口区
# ============================================================

# Import-only adapter; the external workflow runner owns persistence and state.
