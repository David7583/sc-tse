# ============================================================
# 文件名: run_sc_tse_orchestrator_v0003.py
# 中文名: SC-TSE DAG 与阶段准入外部编排器脚本
# 版本号: v0003
#
# 主层级: action
# 层级: development / sc_tse / external_orchestration / dag_runner
# 脚本定位: 只通过公开入口执行 DAG 就绪节点，并把结果交给外部状态机裁决
#
# 职责说明:
# - 调度 Phase 0/1/2/4 与已登记工程 Provider 的 DAG 节点
# - 按需调用 Phase 3 生成反馈或执行外部更新后的单次 re-solve
#
# 本脚本做什么:
# - 支持非机械分支、显式 admission gate、外部更新和 no-overwrite 状态发布
# - 将 Phase 1 布局候选绑定到 Phase 2/4 模板，不增加任何求解算法
# - 为 RoadAccess Provider 物化同一份 Phase 2 Gate 输入并保持 provenance
#
# 本脚本不做什么:
# - 不修改 Core 职责，不自动解释 hint，不自动生成领域规则或新 Problem 语义
# - 不因单节点 FEASIBLE 宣布完成，不绕过 Phase 4 或必需 Provider 失败
#
# 制度边界声明:
# - Phase 3 不属于静态 DAG；仅在 Phase 1/2 失败或外部更新后按需调用
# - 所有输入只读，持久化仅写显式新文件，失败不返回伪完成
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: run_sc_tse_orchestrator_v0003
# family: run_sc_tse_orchestrator
# role: workflow_dag_stage_admission_entry
# version: v0003
# status: active
# entry_point: scripts/action/development/scripts/sc_tse/run_sc_tse_orchestrator_v0003.py
# input:
#   - SC-TSE workflow DAG definition and optional prior state/update JSON
# output:
#   - workflow state with node artifacts, admission decisions and update requests
# depends_on:
#   - sc_tse_orchestration_state_v0002
#   - run_sc_tse_phase0_v0001, run_sc_tse_phase1_v0001, run_sc_tse_phase2_v0001
#   - run_sc_tse_phase3_v0001, run_sc_tse_phase4_v0001
#   - implemented SC-TSE engineering Provider runner modules
# used_by:
#   - test_sc_tse_road_access_golden_v0001
#   - external SC-TSE workflow callers
# ============================================================

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping


for _parent in Path(__file__).resolve().parents:
    if (_parent / "PROGRAMMING_LANGUAGE_SCRIPT_FORMAT.md").is_file():
        PROJECT_ROOT = _parent
        break
else:
    raise RuntimeError("cannot locate project root")
FORMAL_ROOT = PROJECT_ROOT / "scripts" / "action" / "development" / "scripts" / "sc_tse"
for _root in (Path(__file__).resolve().parent, FORMAL_ROOT):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

from run_sc_tse_phase0_v0001 import run_phase0
from run_sc_tse_phase1_v0001 import run_phase1
from run_sc_tse_phase2_v0001 import run_phase2
from run_sc_tse_phase3_v0001 import run_phase3
from run_sc_tse_phase4_v0001 import run_phase4
from sc_tse_orchestration_state_v0002 import (
    ScTseWorkflowStateError,
    apply_external_updates,
    create_workflow_state,
    ready_node_ids,
    record_node_result,
    validate_definition,
)


DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "run_sc_tse_orchestrator"
SCRIPT_NAME = "run_sc_tse_orchestrator_v0003"
SCRIPT_VERSION = "v0003"
RUN_VERSION = "sc-tse-workflow-run-v0.3.0"


# ============================================================
# 异常类型
# ============================================================

class ScTseWorkflowRunnerError(RuntimeError):
    """Base error for DAG execution and public runner dispatch."""


class WorkflowInputError(ScTseWorkflowRunnerError):
    """Raised when a node argument or referenced artifact is unavailable."""


class WorkflowOutputError(ScTseWorkflowRunnerError):
    """Raised when a workflow result cannot be published without overwrite."""


# ============================================================
# 数据结构
# ============================================================

NodeRunner = Callable[[dict[str, Any], Mapping[str, Any], bool], dict[str, Any]]
FeedbackRunner = Callable[[Mapping[str, Any], Mapping[str, Any]], dict[str, Any] | None]
ResolveRunner = Callable[[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]], dict[str, Any]]


# ============================================================
# 工具函数区
# ============================================================

def _read_json(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding=DEFAULT_ENCODING))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowInputError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkflowInputError(f"JSON root must be an object: {path}")
    return value


def _write_new_json(path: str | Path, value: object) -> None:
    target = Path(path).resolve()
    if target.exists() or not target.parent.is_dir():
        raise WorkflowOutputError(f"output must be a new file in an existing directory: {target}")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding=DEFAULT_ENCODING, dir=target.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, target)
    except OSError as exc:
        raise WorkflowOutputError(f"cannot publish output {target}: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _node(definition: Mapping[str, Any], node_id: str) -> dict[str, Any]:
    return next(node for node in definition["nodes"] if node["node_id"] == node_id)


def _resolve(value: object, state: Mapping[str, Any]) -> object:
    if not isinstance(value, Mapping):
        return copy.deepcopy(value)
    if set(value) == {"json_path"}:
        return _read_json(value["json_path"])
    if set(value) == {"file_path"}:
        path = Path(value["file_path"]).resolve()
        if not path.is_file():
            raise WorkflowInputError(f"input file not found: {path}")
        return str(path)
    if set(value) == {"from_node"}:
        source = value["from_node"]
        if source not in state["nodes"] or state["nodes"][source]["status"] != "PASSED":
            raise WorkflowInputError(f"upstream result is not admitted: {source}")
        return copy.deepcopy(state["nodes"][source]["result"])
    return {key: _resolve(item, state) for key, item in value.items()}


def _rectangle_ring(geometry: Mapping[str, Any]) -> list[list[float]]:
    center = geometry.get("center")
    if geometry.get("type") != "rectangle" or geometry.get("rotation_deg", 0) != 0 or not isinstance(center, list):
        raise WorkflowInputError("Phase 4 v0002 adapter currently accepts unrotated rectangle layout blocks")
    x, y = float(center[0]), float(center[1])
    half_width, half_height = float(geometry["width_m"]) / 2, float(geometry["height_m"]) / 2
    return [[x-half_width, y-half_height], [x+half_width, y-half_height], [x+half_width, y+half_height], [x-half_width, y+half_height], [x-half_width, y-half_height]]


def _materialize_phase2(template: dict[str, Any], layout_result: Mapping[str, Any]) -> dict[str, Any]:
    candidate = layout_result.get("layout_candidate")
    if not isinstance(candidate, Mapping):
        raise WorkflowInputError("Phase 2 requires an admitted layout_candidate")
    output = copy.deepcopy(template)
    template_blocks = {block["block_id"]: block for block in output["blocks"]}
    candidate_blocks = {block["block_id"]: block for block in candidate["blocks"]}
    if set(template_blocks) != set(candidate_blocks):
        raise WorkflowInputError("Phase 2 template and layout candidate block identities differ")
    for block_id, block in template_blocks.items():
        block["geometry"] = copy.deepcopy(candidate_blocks[block_id]["geometry"])
        if candidate_blocks[block_id].get("connection_gates"):
            block["connection_gates"] = copy.deepcopy(candidate_blocks[block_id]["connection_gates"])
    output.setdefault("metadata", {})["layout_result_hash"] = state_hash(layout_result)
    return output


def _materialize_phase4(template: dict[str, Any], layout_result: Mapping[str, Any]) -> dict[str, Any]:
    candidate = layout_result.get("layout_candidate")
    if not isinstance(candidate, Mapping):
        raise WorkflowInputError("Phase 4 requires an admitted layout_candidate")
    output = copy.deepcopy(template)
    template_buildings = {building["block_id"]: building for building in output["buildings"]}
    candidate_blocks = {block["block_id"]: block for block in candidate["blocks"]}
    if set(template_buildings) != set(candidate_blocks):
        raise WorkflowInputError("Phase 4 template and layout candidate block identities differ")
    for block_id, building in template_buildings.items():
        building["footprint"] = _rectangle_ring(candidate_blocks[block_id]["geometry"])
    output.setdefault("metadata", {})["layout_result_hash"] = state_hash(layout_result)
    return output


def state_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode(DEFAULT_ENCODING)).hexdigest()


def _temporary_problem(problem: dict[str, Any], directory: Path, node_id: str) -> Path:
    path = directory / f"{node_id}.json"
    path.write_text(json.dumps(problem, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True), encoding=DEFAULT_ENCODING)
    return path


# ============================================================
# 默认映射
# ============================================================

PROVIDER_MODULES = {
    "VERTICAL_CONTRACT": ("run_sc_tse_vertical_contract_v0001", "execute"),
    "TERRAIN_EVIDENCE_IMPORT": ("run_sc_tse_terrain_evidence_import_v0001", "execute"),
    "ELEVATION_BINDING": ("run_sc_tse_elevation_binding_v0001", "execute"),
    "EXISTING_SURFACE": ("run_sc_tse_existing_surface_v0001", "execute"),
    "DESIGN_SURFACE": ("run_sc_tse_design_surface_v0001", "execute"),
    "EARTHWORK": ("run_sc_tse_earthwork_v0001", "execute"),
    "MULTI_ELEVATION": ("run_sc_tse_multi_elevation_v0001", "execute"),
    "SLOPE_TRANSITION": ("run_sc_tse_slope_transition_v0001", "execute"),
    "VEHICLE_SWEPT_PATH": ("run_sc_tse_vehicle_swept_path_v0001", "execute"),
    "ROAD_ACCESS": ("run_sc_tse_road_access_v0001", "execute"),
    "PAD_ELEVATION_OPTIMIZATION": ("run_sc_tse_pad_elevation_optimization_v0001", "execute"),
}


# ============================================================
# 核心类
# ============================================================

def _default_node_runner(node: dict[str, Any], state: Mapping[str, Any], dry_run: bool) -> dict[str, Any]:
    arguments = {key: _resolve(value, state) for key, value in node["arguments"].items()}
    kind = node["kind"]
    if kind == "PHASE0_PRECHECK":
        return run_phase0(arguments["config_path"], arguments["problem_path"], dry_run=dry_run)
    if kind == "PHASE1_LAYOUT":
        return run_phase1(arguments["config_path"], arguments["problem_path"], dry_run=dry_run)
    if kind == "PHASE2_CONNECTIVITY":
        if "problem_template" in arguments:
            problem = _materialize_phase2(arguments["problem_template"], arguments["layout_result"])
            with tempfile.TemporaryDirectory(prefix="sc_tse_phase2_dag_") as temporary:
                problem_path = _temporary_problem(problem, Path(temporary), node["node_id"])
                return run_phase2(arguments["config_path"], problem_path, dry_run=dry_run)
        return run_phase2(arguments["config_path"], arguments["problem_path"], dry_run=dry_run)
    if kind == "PHASE4_ENGINEERING":
        problem = arguments.get("problem")
        if "problem_template" in arguments:
            problem = _materialize_phase4(arguments["problem_template"], arguments["layout_result"])
        return run_phase4(problem, arguments["config"], dry_run=dry_run)
    provider_id = arguments.pop("provider_id", None)
    if provider_id not in PROVIDER_MODULES:
        raise WorkflowInputError(f"unsupported engineering Provider: {provider_id}")
    if provider_id == "ROAD_ACCESS":
        phase2_template = arguments.pop("phase2_problem_template", None)
        layout_result = arguments.pop("layout_result", None)
        if not isinstance(phase2_template, dict) or not isinstance(layout_result, Mapping):
            raise WorkflowInputError("RoadAccess Provider requires Phase 2 template and admitted LayoutCandidate")
        arguments["phase2_problem"] = _materialize_phase2(phase2_template, layout_result)
    module_name, function_name = PROVIDER_MODULES[provider_id]
    function = getattr(importlib.import_module(module_name), function_name)
    return function(**arguments, dry_run=dry_run)


def _phase3_feedback(node: Mapping[str, Any], state: Mapping[str, Any]) -> dict[str, Any] | None:
    if node["kind"] not in {"PHASE1_LAYOUT", "PHASE2_CONNECTIVITY"}:
        return None
    arguments = {key: _resolve(value, state) for key, value in node["arguments"].items()}
    problem_class = "LAYOUT" if node["kind"] == "PHASE1_LAYOUT" else "CONNECTIVITY"
    problem_path = arguments.get("problem_path")
    if problem_path is None and node["kind"] == "PHASE2_CONNECTIVITY":
        problem = _materialize_phase2(arguments["problem_template"], arguments["layout_result"])
        with tempfile.TemporaryDirectory(prefix="sc_tse_phase3_dag_") as temporary:
            path = _temporary_problem(problem, Path(temporary), node["node_id"])
            return run_phase3(arguments["phase3_config_path"], problem_class, path)
    return run_phase3(arguments["phase3_config_path"], problem_class, problem_path)


def _phase3_resolve(
    node: Mapping[str, Any],
    state: Mapping[str, Any],
    previous_feedback: Mapping[str, Any],
) -> dict[str, Any]:
    if node["kind"] not in {"PHASE1_LAYOUT", "PHASE2_CONNECTIVITY"}:
        raise WorkflowInputError("Phase 3 re-solve only supports Phase 1 and Phase 2")
    feedback_batch = previous_feedback.get("feedback_batch")
    if not isinstance(feedback_batch, Mapping):
        raise WorkflowInputError("preserved Phase 3 feedback_batch is required for re-solve")
    arguments = {key: _resolve(value, state) for key, value in node["arguments"].items()}
    problem_class = "LAYOUT" if node["kind"] == "PHASE1_LAYOUT" else "CONNECTIVITY"
    with tempfile.TemporaryDirectory(prefix="sc_tse_phase3_resolve_") as temporary:
        directory = Path(temporary)
        feedback_path = _temporary_problem(dict(feedback_batch), directory, f"{node['node_id']}_feedback")
        problem_path = arguments.get("problem_path")
        if problem_path is None:
            problem = _materialize_phase2(arguments["problem_template"], arguments["layout_result"])
            problem_path = _temporary_problem(problem, directory, node["node_id"])
        return run_phase3(
            arguments["phase3_config_path"],
            problem_class,
            problem_path,
            previous_feedback_path=feedback_path,
        )


def run_workflow(
    definition: Mapping[str, Any] | None = None,
    *,
    state: Mapping[str, Any] | None = None,
    updates: Mapping[str, Any] | None = None,
    max_node_executions: int | None = None,
    dry_run: bool = False,
    node_runner: NodeRunner = _default_node_runner,
    feedback_runner: FeedbackRunner = _phase3_feedback,
    resolve_runner: ResolveRunner = _phase3_resolve,
) -> dict[str, Any]:
    if state is None:
        if definition is None:
            raise WorkflowInputError("definition is required for a new workflow")
        current = create_workflow_state(definition)
    else:
        current = copy.deepcopy(dict(state))
        if definition is not None:
            raise WorkflowInputError("definition must come from resumed state")
    if updates:
        current = apply_external_updates(current, updates)
    if dry_run:
        validate_definition(current["definition"])
        return {"schema_version": RUN_VERSION, "mode": "DRY_RUN", "workflow_state": current, "executed_node_ids": []}
    executed: list[str] = []
    limit = max_node_executions if max_node_executions is not None else len(current["definition"]["nodes"])
    while len(executed) < limit:
        ready = ready_node_ids(current)
        if not ready:
            break
        node_id = ready[0]
        node = _node(current["definition"], node_id)
        try:
            previous_feedback = current["nodes"][node_id]["feedback"]
            if previous_feedback is not None and node["kind"] in {"PHASE1_LAYOUT", "PHASE2_CONNECTIVITY"}:
                feedback = resolve_runner(node, current, previous_feedback)
                computational_result = feedback.get("computational_result")
                if not isinstance(computational_result, Mapping):
                    raise WorkflowInputError("Phase 3 re-solve did not return computational_result")
                result = dict(computational_result)
            else:
                result = node_runner(node, current, False)
                feedback = None
                if node["kind"] in {"PHASE1_LAYOUT", "PHASE2_CONNECTIVITY"} and not (
                    result.get("execution_status") == "COMPLETED" and result.get("solve_status") == "FEASIBLE"
                ):
                    feedback = feedback_runner(node, current)
        except Exception as exc:
            result = {"execution_status": "ERROR", "solve_status": "NOT_EVALUATED", "diagnostics": {"error_type": type(exc).__name__, "detail": str(exc)}}
            feedback = None
        current = record_node_result(current, node_id, result, feedback)
        executed.append(node_id)
    return {"schema_version": RUN_VERSION, "mode": "RUN", "workflow_state": current, "executed_node_ids": executed}


# ============================================================
# Schema / 契约辅助函数
# ============================================================

# Public Core and Provider runners continue to validate their own result contracts.


# ============================================================
# CLI / main 接口区
# ============================================================

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run or resume one SC-TSE workflow DAG.")
    parser.add_argument("--definition")
    parser.add_argument("--state")
    parser.add_argument("--updates")
    parser.add_argument("--max-node-executions", type=int)
    parser.add_argument("--output")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _build_parser().parse_args(argv)
        result = run_workflow(
            _read_json(args.definition) if args.definition else None,
            state=_read_json(args.state) if args.state else None,
            updates=_read_json(args.updates) if args.updates else None,
            max_node_executions=args.max_node_executions,
            dry_run=bool(args.dry_run),
        )
        if args.output and not args.dry_run:
            _write_new_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True))
        return 0
    except (ScTseWorkflowRunnerError, ScTseWorkflowStateError, OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "error_type": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False))
        return 2
    except Exception as exc:
        print(json.dumps({"status": "error", "error_type": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
