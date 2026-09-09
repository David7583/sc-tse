# ============================================================
# 文件名: sc_tse_orchestration_state_v0002.py
# 中文名: SC-TSE 工作流 DAG 与阶段准入状态机脚本
# 版本号: v0002
#
# 主层级: action
# 层级: development / sc_tse / external_orchestration / dag_state
# 脚本定位: 位于所有 SC-TSE Core 与工程 Provider 外部的 DAG、准入门和状态所有者
#
# 职责说明:
# - 校验首版端到端 workflow DAG、依赖关系和阶段准入规则
# - 记录节点结果、外部更新请求，并只在全部必需门通过后完成工作流
#
# 本脚本做什么:
# - 支持 Phase 0/1/2/4 与工程 Provider 节点及非机械分支执行
# - 支持外部更新后定向重置节点及其下游，保留可追溯结果哈希
#
# 本脚本不做什么:
# - 不导入或调用任何 Core、Solver、Feedback Generator 或 Provider
# - 不把 Phase 3 固定进 DAG，不修改 Problem，不执行反馈 hint
#
# 制度边界声明:
# - 单个 Phase FEASIBLE 永远不能单独产生 workflow COMPLETED
# - Phase 4 必需节点失败必须阻断完成并进入外部更新状态
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: sc_tse_orchestration_state_v0002
# family: sc_tse_orchestration_state
# role: workflow_dag_and_stage_admission_state_machine
# version: v0002
# status: active
# entry_point: scripts/action/development/scripts/sc_tse/sc_tse_orchestration_state_v0002.py
# input:
#   - versioned workflow DAG definition
#   - existing Phase and engineering Provider results
# output:
#   - versioned workflow state and stage admission decisions
# depends_on:
#   - Python stdlib: copy, hashlib, json, typing
# used_by:
#   - run_sc_tse_orchestrator_v0002
#   - test_sc_tse_orchestrator_v0002
# ============================================================

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping


DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "sc_tse_orchestration_state"
SCRIPT_NAME = "sc_tse_orchestration_state_v0002"
SCRIPT_VERSION = "v0002"
DEFINITION_VERSION = "sc-tse-workflow-dag-v0.1.0"
STATE_VERSION = "sc-tse-workflow-state-v0.2.0"


# ============================================================
# 异常类型
# ============================================================

class ScTseWorkflowStateError(RuntimeError):
    """Base error for DAG workflow state and admission handling."""


class WorkflowDefinitionError(ScTseWorkflowStateError):
    """Raised when a workflow definition is malformed or cyclic."""


class StageAdmissionError(ScTseWorkflowStateError):
    """Raised when a stage result cannot be evaluated safely."""


class WorkflowUpdateError(ScTseWorkflowStateError):
    """Raised when an external update targets an unavailable workflow node."""


# ============================================================
# 数据结构
# ============================================================

# Definitions, results and state remain primitive JSON mappings.


# ============================================================
# 工具函数区
# ============================================================

def canonical_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode(DEFAULT_ENCODING)).hexdigest()


def _nonempty(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowDefinitionError(f"{field} must be a non-empty string")
    return value


def _result_value(result: Mapping[str, Any], path: str) -> object:
    value: object = result
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            raise StageAdmissionError(f"result field is missing: {path}")
        value = value[part]
    return value


def _descendants(definition: Mapping[str, Any], roots: set[str]) -> set[str]:
    affected = set(roots)
    changed = True
    while changed:
        changed = False
        for node in definition["nodes"]:
            if node["node_id"] not in affected and set(node["depends_on"]) & affected:
                affected.add(node["node_id"])
                changed = True
    return affected


# ============================================================
# 默认映射
# ============================================================

CORE_KINDS = {"PHASE0_PRECHECK", "PHASE1_LAYOUT", "PHASE2_CONNECTIVITY", "PHASE4_ENGINEERING"}
NODE_KINDS = CORE_KINDS | {"ENGINEERING_PROVIDER"}
NODE_STATUSES = {"PENDING", "RUNNING", "PASSED", "WAITING_EXTERNAL_UPDATE", "OPTIONAL_FAILED"}


# ============================================================
# 核心类
# ============================================================

def validate_definition(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise WorkflowDefinitionError("workflow definition must be an object")
    definition = copy.deepcopy(dict(value))
    if set(definition) != {"schema_version", "workflow_id", "nodes"}:
        raise WorkflowDefinitionError("workflow definition keys must be schema_version, workflow_id, nodes")
    if definition["schema_version"] != DEFINITION_VERSION:
        raise WorkflowDefinitionError("unsupported workflow definition version")
    _nonempty(definition["workflow_id"], "workflow_id")
    if not isinstance(definition["nodes"], list) or not definition["nodes"]:
        raise WorkflowDefinitionError("nodes must be a non-empty array")
    identifiers: set[str] = set()
    counts = {kind: 0 for kind in CORE_KINDS}
    for index, node in enumerate(definition["nodes"]):
        if not isinstance(node, dict) or set(node) != {"node_id", "kind", "depends_on", "required_for_completion", "arguments", "admission"}:
            raise WorkflowDefinitionError(f"node {index} has invalid keys")
        node_id = _nonempty(node["node_id"], f"nodes[{index}].node_id")
        if node_id in identifiers:
            raise WorkflowDefinitionError(f"duplicate node_id: {node_id}")
        identifiers.add(node_id)
        kind = node["kind"]
        if kind == "PHASE3_FEEDBACK" or kind not in NODE_KINDS:
            raise WorkflowDefinitionError("Phase 3 is on-demand only and cannot be a static DAG node")
        if kind in counts:
            counts[kind] += 1
        if not isinstance(node["depends_on"], list) or len(set(node["depends_on"])) != len(node["depends_on"]):
            raise WorkflowDefinitionError(f"{node_id}.depends_on must be a unique array")
        if not isinstance(node["required_for_completion"], bool):
            raise WorkflowDefinitionError(f"{node_id}.required_for_completion must be boolean")
        if kind in CORE_KINDS and not node["required_for_completion"]:
            raise WorkflowDefinitionError(f"core stage {node_id} must be required for completion")
        if not isinstance(node["arguments"], dict):
            raise WorkflowDefinitionError(f"{node_id}.arguments must be an object")
        if kind == "ENGINEERING_PROVIDER" and not isinstance(node["admission"], list):
            raise WorkflowDefinitionError(f"provider {node_id} requires explicit admission conditions")
        if kind != "ENGINEERING_PROVIDER" and node["admission"] is not None:
            raise WorkflowDefinitionError(f"{node_id} uses the built-in stage admission gate")
    if any(counts[kind] != 1 for kind in CORE_KINDS):
        raise WorkflowDefinitionError("first closure requires exactly one Phase 0, 1, 2 and Phase 4 node")
    node_map = {node["node_id"]: node for node in definition["nodes"]}
    for node in definition["nodes"]:
        if any(dependency not in node_map for dependency in node["depends_on"]):
            raise WorkflowDefinitionError(f"{node['node_id']} has an unknown dependency")
        if node["node_id"] in node["depends_on"]:
            raise WorkflowDefinitionError(f"{node['node_id']} cannot depend on itself")
    pending = set(node_map)
    resolved: set[str] = set()
    while pending:
        ready = {node_id for node_id in pending if set(node_map[node_id]["depends_on"]) <= resolved}
        if not ready:
            raise WorkflowDefinitionError("workflow DAG contains a cycle")
        resolved.update(ready)
        pending.difference_update(ready)
    phase0 = next(node["node_id"] for node in definition["nodes"] if node["kind"] == "PHASE0_PRECHECK")
    phase1 = next(node["node_id"] for node in definition["nodes"] if node["kind"] == "PHASE1_LAYOUT")
    phase2 = next(node["node_id"] for node in definition["nodes"] if node["kind"] == "PHASE2_CONNECTIVITY")
    phase4 = next(node["node_id"] for node in definition["nodes"] if node["kind"] == "PHASE4_ENGINEERING")
    if phase1 not in _descendants(definition, {phase0}) or phase2 not in _descendants(definition, {phase1}) or phase4 not in _descendants(definition, {phase1}):
        raise WorkflowDefinitionError("Phase 1 must follow precheck; Phase 2 and Phase 4 must depend on layout")
    return definition


def create_workflow_state(definition_value: Mapping[str, Any]) -> dict[str, Any]:
    definition = validate_definition(definition_value)
    return {
        "schema_version": STATE_VERSION,
        "workflow_id": definition["workflow_id"],
        "revision": 0,
        "status": "RUNNING",
        "definition": definition,
        "definition_hash": canonical_hash(definition),
        "nodes": {
            node["node_id"]: {
                "status": "PENDING",
                "attempts": 0,
                "result": None,
                "result_hash": None,
                "gate": None,
                "feedback": None,
            }
            for node in definition["nodes"]
        },
        "external_update_requests": [],
    }


def ready_node_ids(state: Mapping[str, Any]) -> list[str]:
    definition = validate_definition(state["definition"])
    ready: list[str] = []
    for node in definition["nodes"]:
        node_state = state["nodes"][node["node_id"]]
        if node_state["status"] != "PENDING":
            continue
        if all(state["nodes"][dependency]["status"] == "PASSED" for dependency in node["depends_on"]):
            ready.append(node["node_id"])
    return ready


def evaluate_admission(node: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
    kind = node["kind"]
    if kind == "PHASE0_PRECHECK":
        passed = result.get("execution_status") == "COMPLETED"
        reason = "PRECHECK_COMPLETED" if passed else "PRECHECK_FAILED"
    elif kind in {"PHASE1_LAYOUT", "PHASE2_CONNECTIVITY"}:
        passed = result.get("execution_status") == "COMPLETED" and result.get("solve_status") == "FEASIBLE"
        reason = "STAGE_FEASIBLE" if passed else "STAGE_NOT_ADMITTED"
    elif kind == "PHASE4_ENGINEERING":
        candidate = result.get("candidate")
        passed = (
            result.get("execution_status") == "COMPLETED"
            and result.get("solve_status") == "FEASIBLE"
            and isinstance(candidate, Mapping)
            and candidate.get("hard_constraint_status") == "SATISFIED"
        )
        reason = "PHASE4_ENGINEERING_ADMITTED" if passed else "PHASE4_ENGINEERING_BLOCKED"
    else:
        passed = True
        for condition in node["admission"]:
            if not isinstance(condition, dict) or set(condition) != {"path", "accepted"} or not isinstance(condition["accepted"], list):
                raise StageAdmissionError(f"invalid provider admission condition for {node['node_id']}")
            if _result_value(result, condition["path"]) not in condition["accepted"]:
                passed = False
        reason = "PROVIDER_ADMITTED" if passed else "PROVIDER_BLOCKED"
    return {"passed": passed, "reason": reason, "result_hash": canonical_hash(result)}


def record_node_result(state_value: Mapping[str, Any], node_id: str, result: Mapping[str, Any], feedback: Mapping[str, Any] | None = None) -> dict[str, Any]:
    state = copy.deepcopy(dict(state_value))
    node = next((item for item in state["definition"]["nodes"] if item["node_id"] == node_id), None)
    if node is None or state["nodes"][node_id]["status"] != "PENDING":
        raise StageAdmissionError(f"node is not pending: {node_id}")
    gate = evaluate_admission(node, result)
    node_state = state["nodes"][node_id]
    node_state.update({
        "status": "PASSED" if gate["passed"] else ("WAITING_EXTERNAL_UPDATE" if node["required_for_completion"] else "OPTIONAL_FAILED"),
        "attempts": node_state["attempts"] + 1,
        "result": copy.deepcopy(dict(result)),
        "result_hash": gate["result_hash"],
        "gate": gate,
        "feedback": copy.deepcopy(dict(feedback)) if feedback is not None else None,
    })
    if not gate["passed"] and node["required_for_completion"]:
        state["external_update_requests"].append({
            "node_id": node_id,
            "kind": node["kind"],
            "reason": gate["reason"],
            "result_hash": gate["result_hash"],
            "feedback_available": feedback is not None,
        })
    state["revision"] += 1
    state["status"] = workflow_status(state)
    return state


def workflow_status(state: Mapping[str, Any]) -> str:
    required = [node for node in state["definition"]["nodes"] if node["required_for_completion"]]
    statuses = [state["nodes"][node["node_id"]]["status"] for node in required]
    if all(status == "PASSED" for status in statuses):
        return "COMPLETED"
    if any(status == "WAITING_EXTERNAL_UPDATE" for status in statuses):
        return "WAITING_FOR_EXTERNAL_UPDATE"
    return "RUNNING"


def apply_external_updates(state_value: Mapping[str, Any], updates: Mapping[str, Any]) -> dict[str, Any]:
    state = copy.deepcopy(dict(state_value))
    if state.get("schema_version") != STATE_VERSION or not isinstance(updates, Mapping) or not updates:
        raise WorkflowUpdateError("valid state and at least one update are required")
    node_map = {node["node_id"]: node for node in state["definition"]["nodes"]}
    unknown = set(updates) - set(node_map)
    if unknown:
        raise WorkflowUpdateError(f"unknown update nodes: {sorted(unknown)}")
    affected = _descendants(state["definition"], set(updates))
    for node_id, arguments in updates.items():
        if not isinstance(arguments, dict):
            raise WorkflowUpdateError(f"update for {node_id} must be an arguments object")
        node_map[node_id]["arguments"].update(copy.deepcopy(arguments))
    state["definition"] = validate_definition(state["definition"])
    state["definition_hash"] = canonical_hash(state["definition"])
    for node_id in affected:
        previous_feedback = state["nodes"][node_id]["feedback"] if node_id in updates else None
        attempts = state["nodes"][node_id]["attempts"]
        state["nodes"][node_id] = {
            "status": "PENDING", "attempts": attempts, "result": None, "result_hash": None,
            "gate": None, "feedback": previous_feedback,
        }
    state["external_update_requests"] = [item for item in state["external_update_requests"] if item["node_id"] not in affected]
    state["revision"] += 1
    state["status"] = "RUNNING"
    return state


# ============================================================
# Schema / 契约辅助函数
# ============================================================

# validate_definition and evaluate_admission are the public v0002 contracts.


# ============================================================
# CLI / main 接口区
# ============================================================

# This pure state component intentionally has no CLI.
