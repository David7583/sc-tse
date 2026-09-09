# ============================================================
# 文件名: sc_tse_integration_api_v0001.py
# 中文名: SC-TSE Integration MVP 请求适配接口
# 版本号: v0001
#
# 主层级: action
# 层级: development / sc_tse / frontend / backend_adapter
# 脚本定位: 结构化项目与坐标编辑到唯一 v0004 主链的适配边界
#
# 职责说明:
# - 物化同源输入并返回完整工作流及展示数据
#
# 本脚本做什么:
# - 校验项目、平移输入 Gate、调用一次 v0004、提取真实结果
#
# 本脚本不做什么:
# - 不实现求解、道路生成、工程校核或自主重试
#
# 制度边界声明:
# - 原始项目只读；中间输入仅写 TemporaryDirectory
# - 缺失结果保持 null；不使用旧结果或其他案例补齐
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: sc_tse_integration_api_v0001
# family: sc_tse_integration_api
# role: integration_request_adapter
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/sc_tse/frontend/sc_tse_integration_api_v0001.py
# input:
#   - sc-tse-integration-request-v0001
# output:
#   - sc-tse-integration-result-v0001
# depends_on:
#   - run_sc_tse_orchestrator_v0004
#   - jsonschema
# used_by:
#   - run_sc_tse_integration_v0001
# ============================================================

from __future__ import annotations

import copy
import hashlib
import json
import math
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "sc_tse_integration_api"
SCRIPT_NAME = "sc_tse_integration_api_v0001"
SCRIPT_VERSION = "v0001"
BASE = Path(__file__).resolve().parent
PROJECT_ROOT = next(p for p in BASE.parents if (p / "PROGRAMMING_LANGUAGE_SCRIPT_FORMAT.md").is_file())
CORE = PROJECT_ROOT / "scripts/action/development/scripts/sc_tse"
sys.path.insert(0, str(CORE))
from run_sc_tse_orchestrator_v0004 import run_workflow


# ============================================================
# 异常类型 / 数据结构
# ============================================================

class IntegrationInputError(ValueError):
    """Invalid versioned input; no workflow has run."""


# Public wire types are specified in integration_schemas/contract_v0001.json.


# ============================================================
# 工具函数区
# ============================================================

def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding=DEFAULT_ENCODING))


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def validate(value: Any, contract: str) -> None:
    schema = read_json(BASE / "integration_schemas/contract_v0001.json")
    wrapper = {"$ref": f"#/$defs/{contract}", "$defs": schema["$defs"]}
    errors = sorted(Draft202012Validator(wrapper).iter_errors(value), key=lambda e: str(e.path))
    if errors:
        raise IntegrationInputError(f"{list(errors[0].path)}: {errors[0].message}")
    try:
        digest(value)
    except (ValueError, TypeError) as exc:
        raise IntegrationInputError("JSON must contain finite numbers only") from exc


def load_project() -> dict[str, Any]:
    project = read_json(BASE / "integration_cases/gc_road_001_v0001.json")
    validate_project(project)
    return project


def validate_project(project: dict[str, Any]) -> None:
    validate(project, "project")
    def reject_references(value):
        if isinstance(value, dict):
            if set(value) & {"json_path", "file_path", "from_node"}:
                raise IntegrationInputError("workflow references are server-owned, not project input")
            for item in value.values():
                reject_references(item)
        elif isinstance(value, list):
            for item in value:
                reject_references(item)
    reject_references(project)
    case = project["case"]
    identifiers = [b["block_id"] for b in case["blocks"]]
    if len(identifiers) != len(set(identifiers)):
        raise IntegrationInputError("duplicate block_id")
    if case["coordinate_frame"] != read_json(PROJECT_ROOT / "config/action/config/sc_tse_phase0_config_v0001.json")["internal_coordinate_frame"]:
        raise IntegrationInputError("coordinate frame must match the current kernel")
    road = project["road_problem_template"]
    if (road.get("problem_id") != case["problem_id"] or
            project["road_graph_request"].get("problem_id") != case["problem_id"] or
            road.get("site", {}).get("site_id") != case["site_id"] or
            road.get("site", {}).get("boundary") != case["site_geometry"]["coordinates"] or
            road.get("coordinate_frame") != case["coordinate_frame"]):
        raise IntegrationInputError("project identities, coordinate frame and Site must agree")
    if {b.get("block_id") for b in road.get("buildings", [])} != set(identifiers):
        raise IntegrationInputError("road template block identities differ")


def edited_case(project: dict[str, Any], edits: list[dict[str, Any]]) -> dict[str, Any]:
    case = copy.deepcopy(project["case"])
    blocks = {b["block_id"]: b for b in case["blocks"]}
    seen = set()
    for edit in edits:
        identity = edit["block_id"]
        if identity in seen or identity not in blocks:
            raise IntegrationInputError("unknown or duplicate edited block_id")
        seen.add(identity)
        block = blocks[identity]
        old = block["geometry"]["center"]
        center = [edit["center_x"], edit["center_y"]]
        delta = [center[i] - old[i] for i in (0, 1)]
        # Input translation only: preserve each Gate's offset on its owning Block.
        for gate in block["connection_gates"]:
            gate["position"] = [gate["position"][i] + delta[i] for i in (0, 1)]
        block["geometry"]["center"] = center
    return case


def problems(case: dict[str, Any]) -> tuple[dict, dict, dict]:
    common = {"problem_id": case["problem_id"], "coordinate_frame": case["coordinate_frame"],
              "site": {"site_id": case["site_id"], "geometry": case["site_geometry"], "metadata": {}},
              "blocks": case["blocks"], "metadata": {"integration_input_hash": digest(case)}}
    p0 = {**copy.deepcopy(common), "schema_version": "sc-tse-computational-problem-v0.1.0",
          "constraints": case["constraints"], "connection_requirements": case["connection_requirements"]}
    p1 = {**copy.deepcopy(common), "schema_version": "sc-tse-layout-problem-v0.1.0",
          "constraints": case["constraints"], "connection_requirements": [],
          "variable_controls": [{"object_id": b["block_id"], "variable": "position", "mode": "FIXED",
                                 "source": {"policy": "USER_COORDINATES_FIXED_FOR_THIS_RUN"}, "metadata": {}}
                                for b in case["blocks"]],
          "solver_config": {"strategy": "DETERMINISTIC_GRID_SCAN", "placement_domain": "INITIAL_PLUS_GRID_CENTERS",
                            "grid_step_m": 10, "max_candidate_evaluations": 1000}}
    for block in p1["blocks"]:
        block["connection_gates"] = []
    p2 = {**copy.deepcopy(common), "schema_version": "sc-tse-connectivity-problem-v0.1.0",
          "connection_requirements": case["connection_requirements"],
          "obstacle_buffers": [{"block_id": b["block_id"], "buffer_m": case["obstacle_buffer_m"]} for b in case["blocks"]],
          "solver_config": case["solver_config"]}
    return p0, p1, p2


def workflow_definition(project: dict, case: dict, directory: Path) -> dict:
    p0, p1, p2 = problems(case)
    for name, problem in (("phase0", p0), ("phase1", p1)):
        (directory / f"{name}.json").write_text(json.dumps(problem), encoding=DEFAULT_ENCODING)
    def cfg(name):
        return str(PROJECT_ROOT / f"config/action/config/sc_tse_{name}_config_v0001.json")
    def node(identity, kind, parents, arguments, admission=None):
        return {"node_id": identity, "kind": kind, "depends_on": parents,
                "required_for_completion": True, "arguments": arguments, "admission": admission}
    layout = {"from_node": "layout"}
    common = {"phase2_problem_template": p2, "layout_result": layout}
    return {"schema_version": "sc-tse-workflow-dag-v0.1.0", "workflow_id": project["project_id"], "nodes": [
        node("precheck", "PHASE0_PRECHECK", [], {"config_path": cfg("phase0"), "problem_path": str(directory / "phase0.json")}),
        node("layout", "PHASE1_LAYOUT", ["precheck"], {"config_path": cfg("phase1"), "phase3_config_path": cfg("phase3"), "problem_path": str(directory / "phase1.json")}),
        node("connectivity", "PHASE2_CONNECTIVITY", ["layout"], {"config_path": cfg("phase2"), "phase3_config_path": cfg("phase3"), "problem_template": p2, "layout_result": layout}),
        node("road_graph", "ENGINEERING_PROVIDER", ["connectivity", "layout"], {**common,
             "provider_id": "BASIC_ROAD_NETWORK", "phase2_result": {"from_node": "connectivity"},
             "request": project["road_graph_request"], "config": read_json(Path(cfg("road_graph"))),
             "phase2_config_path": cfg("phase2")}, [{"path": "hard_constraint_status", "accepted": ["SATISFIED"]}]),
        node("engineering", "PHASE4_ENGINEERING", ["road_graph", "layout"], {
             "problem_template": project["road_problem_template"], "layout_result": layout,
             "road_graph_run": {"from_node": "road_graph"},
             "phase4_config": read_json(Path(cfg("road_graph_engineering_phase4"))),
             "adapter_config": read_json(Path(cfg("road_graph_phase4")))}),
        node("access", "ENGINEERING_PROVIDER", ["engineering", "road_graph", "layout"], {**common,
             "provider_id": "ROAD_GRAPH_ACCESS", "road_graph_run": {"from_node": "road_graph"},
             "engineering_run": {"from_node": "engineering"}}, [{"path": "evaluation_status", "accepted": ["PASS"]}]),
    ]}


def scene(case: dict, results: dict | None = None) -> dict:
    results = results or {}
    candidate = (results.get("layout") or {}).get("layout_candidate")
    blocks = candidate["blocks"] if candidate else case["blocks"]
    graph = (results.get("road_graph") or {}).get("road_graph")
    corridors = (results.get("engineering") or {}).get("corridor_runs", [])
    # Pass through actual centerline samples, with no route synthesis or smoothing.
    roads = []
    for corridor in corridors:
        road_candidate = corridor["road_run"].get("candidate")
        if road_candidate:
            samples = road_candidate.get("alignment", {}).get("samples", [])
            roads.append({"id": corridor["polyline_id"], "coordinates": [s["xy"] for s in samples],
                          "admission_status": corridor["admission_status"]})
    return {"site": case["site_geometry"], "blocks": blocks,
            "layout_source": "COMPUTED_LAYOUT" if candidate else "EXPLICIT_INPUT",
            "road_graph": graph, "engineered_roads": roads,
            "access_connections": (results.get("access") or {}).get("access_connections", [])}


# ============================================================
# 核心类 / 公开接口
# ============================================================

def run_request(request: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    validate(request, "request")
    project = request["project"]
    validate_project(project)
    case = edited_case(project, request["edits"])
    with tempfile.TemporaryDirectory(prefix="sc_tse_integration_") as temporary:
        definition = workflow_definition(project, case, Path(temporary))
        workflow = run_workflow(definition)  # The only backend computational entry.
    state = workflow["workflow_state"]
    results = {key: item["result"] for key, item in state["nodes"].items()}
    blocked = []
    for identity, item in state["nodes"].items():
        if item["status"] == "WAITING_EXTERNAL_UPDATE":
            result = item["result"] or {}
            blocked.append({"node_id": identity, "gate": item["gate"], "diagnostics": result.get("diagnostics"),
                            "failure_data": result.get("failure_data"), "feedback": item["feedback"]})
    graph = (results["road_graph"] or {}).get("road_graph")
    output = {"schema_version": "sc-tse-integration-result-v0001", "request_id": request["request_id"],
              "status": state["status"], "project_id": project["project_id"], "input_hash": digest(request),
              "case_hash": digest(case), "scene": scene(case, results), "blocking_nodes": blocked,
              "metrics": {"elapsed_ms": (time.perf_counter() - started) * 1000,
                          "road_edges": len(graph["edges"]) if graph else None,
                          "road_cycles": graph["cycle_count"] if graph else None,
                          "access_status": (results["access"] or {}).get("evaluation_status")},
              "results": results, "workflow": workflow}
    output["result_hash"] = digest(output)
    validate(output, "result")
    return output


# ============================================================
# CLI / main 接口区
# ============================================================

# Import-only. HTTP transport lives in run_sc_tse_integration_v0001.py.
