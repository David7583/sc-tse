# ============================================================
# 文件名: run_sc_tse_road_graph_access_v0001.py
# 中文名: RoadGraph 道路接入验证入口
# 版本号: v0001
#
# 主层级: action
# 层级: development / sc_tse / road_graph_access_provider
# 脚本定位: 验证 RoadGraph 的场地入口、建筑 Gate 与工程道路接入
#
# 职责说明:
# - 对生成器输出的 AccessConnection 和 Phase 4 工程道路执行只读验证
#
# 本脚本做什么:
# - 区分 Road-SiteEntrance、Road-BuildingGate、Road-RoadTopology 并传播失败
#
# 本脚本不做什么:
# - 不生成或修补接入线，不移动 Gate，不拥有工作流终态
#
# 制度边界声明:
# - 所有几何均来自已准入 artifact；Renderer 不参与验证
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: run_sc_tse_road_graph_access_v0001
# family: run_sc_tse_road_graph_access
# role: road_graph_access_validator
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/sc_tse/run_sc_tse_road_graph_access_v0001.py
# input:
#   - Phase 2 problem, RoadGraphRun and RoadGraphEngineeringRun
# output:
#   - RoadGraphAccessRun with explicit three-relation evidence
# depends_on:
#   - Shapely 2.x
# used_by:
#   - run_sc_tse_orchestrator_v0004
#   - test_sc_tse_basic_road_network_v0001
# ============================================================

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Mapping

import jsonschema
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "run_sc_tse_road_graph_access"
SCRIPT_NAME = "run_sc_tse_road_graph_access_v0001"
SCRIPT_VERSION = "v0001"
RESULT_VERSION = "sc-tse-road-graph-access-result-v0.1.0"


# ============================================================
# 异常类型
# ============================================================

class RoadGraphAccessError(RuntimeError):
    """Raised when RoadGraph access inputs are not admitted or consistent."""


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


# ============================================================
# 核心类
# ============================================================

def validate_road_graph_access(
    phase2_problem: dict[str, Any],
    road_graph_run: dict[str, Any],
    engineering_run: dict[str, Any],
    *,
    tolerance_m: float = 0.01,
    dry_run: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    _validate(phase2_problem, "sc_tse_connectivity_problem_v0001.schema.json")
    _validate(road_graph_run, "sc_tse_road_graph_result_v0001.schema.json")
    _validate(engineering_run, "sc_tse_road_graph_engineering_result_v0001.schema.json")
    if road_graph_run["problem_id"] != phase2_problem["problem_id"] or engineering_run["problem_id"] != phase2_problem["problem_id"]:
        raise RoadGraphAccessError("problem identity differs across RoadGraph access artifacts")
    if engineering_run.get("solve_status") != "FEASIBLE" or engineering_run.get("hard_constraint_status") != "SATISFIED":
        raise RoadGraphAccessError("RoadGraph access requires an admitted Phase 4 engineering result")
    if dry_run:
        return {"schema_version": RESULT_VERSION, "record_type": "RoadGraphAccessDryRun", "status": "dry_run", "problem_id": phase2_problem["problem_id"]}
    site = Polygon(phase2_problem["site"]["geometry"]["coordinates"])
    buildings = {
        item["block_id"]: Polygon(item["geometry"]["coordinates"])
        if item["geometry"]["type"] == "polygon"
        else Polygon([
            [item["geometry"]["center"][0] - item["geometry"]["width_m"] / 2, item["geometry"]["center"][1] - item["geometry"]["height_m"] / 2],
            [item["geometry"]["center"][0] + item["geometry"]["width_m"] / 2, item["geometry"]["center"][1] - item["geometry"]["height_m"] / 2],
            [item["geometry"]["center"][0] + item["geometry"]["width_m"] / 2, item["geometry"]["center"][1] + item["geometry"]["height_m"] / 2],
            [item["geometry"]["center"][0] - item["geometry"]["width_m"] / 2, item["geometry"]["center"][1] + item["geometry"]["height_m"] / 2],
        ])
        for item in phase2_problem["blocks"]
    }
    gate_owners = {
        gate["gate_id"]: block["block_id"]
        for block in phase2_problem["blocks"]
        for gate in block["connection_gates"]
    }
    engineered_lines = []
    for corridor in engineering_run["corridor_runs"]:
        samples = corridor["road_run"]["candidate"]["alignment"]["samples"]
        engineered_lines.append(LineString([item["xy"] for item in samples]))
    road_network = unary_union(engineered_lines)
    connections = []
    failures = []
    for access in road_graph_run["served_gates"]:
        line = LineString(access["coordinates"])
        relation = "Road-SiteEntrance" if access["gate_kind"] == "SITE_ENTRANCE" else "Road-BuildingGate"
        source_valid = site.boundary.distance(Point(access["gate_position"])) <= tolerance_m
        owner = gate_owners.get(access["gate_id"])
        if relation == "Road-BuildingGate":
            source_valid = owner is not None and buildings[owner].boundary.distance(Point(access["gate_position"])) <= tolerance_m
        crossing = [block_id for block_id, footprint in buildings.items() if line.intersection(footprint).length > tolerance_m]
        target_distance = road_network.distance(Point(access["network_position"]))
        connected = source_valid and not crossing and site.buffer(tolerance_m).covers(line) and target_distance <= tolerance_m
        item = {
            "access_id": f"ACCESS-{access['gate_id']}",
            "relation_type": relation,
            "source_id": access["gate_id"],
            "block_id": owner,
            "status": "CONNECTED" if connected else "NOT_CONNECTED",
            "coordinates": access["coordinates"],
            "network_position": access["network_position"],
            "checks": {
                "source_identity_and_position": source_valid,
                "inside_site": site.buffer(tolerance_m).covers(line),
                "avoids_building_interiors": not crossing,
                "target_on_engineered_road": target_distance <= tolerance_m,
            },
            "source_node": access["source_node"],
        }
        connections.append(item)
        if not connected:
            failures.append({"access_id": item["access_id"], "reason": "REQUIRED_ACCESS_NOT_CONNECTED"})
    topology = {
        "relation_type": "Road-RoadTopology",
        "connected_component_count": engineering_run["road_graph_topology"]["connected_component_count"],
        "cycle_count": engineering_run["road_graph_topology"]["cycle_count"],
        "status": engineering_run["road_graph_topology"]["status"],
    }
    if topology["status"] != "SATISFIED":
        failures.append({"access_id": None, "reason": "ROAD_GRAPH_TOPOLOGY_NOT_SATISFIED"})
    result = {
        "schema_version": RESULT_VERSION,
        "record_type": "RoadGraphAccessRun",
        "execution_status": "COMPLETED",
        "evaluation_status": "PASS" if not failures else "FAIL",
        "problem_id": phase2_problem["problem_id"],
        "road_graph_id": road_graph_run["road_graph_id"],
        "road_topology": topology,
        "access_connections": connections,
        "failure_data": failures,
        "source_refs": {
            "phase2_problem_hash": _hash(phase2_problem),
            "road_graph_result_hash": _hash(road_graph_run),
            "engineering_result_hash": _hash(engineering_run),
        },
        "observability": {"total_ms": (time.perf_counter() - started) * 1000.0, "access_count": len(connections)},
    }
    _validate(result, "sc_tse_road_graph_access_result_v0001.schema.json")
    return result


# ============================================================
# CLI / main 接口区
# ============================================================

# Import-only validator; the workflow runner owns state and final admission.

