# ============================================================
# 文件名: sc_tse_feedback_generator_v0001.py
# 中文名: SC-TSE Phase 3 LayoutFeedback 生成脚本
# 版本号: v0001
#
# 主层级: action
# 层级: development / sc_tse / phase3_feedback_generation
# 脚本定位: 已校验计算结果到统一 LayoutFeedback Batch 的确定性转换边界
#
# 职责说明:
# - 将 Phase 1 约束失败和 Phase 2 连通失败转换为统一 LayoutFeedback
# - 为反馈建立稳定身份、受影响对象、严重度、证据和非强制 hint
#
# 本脚本做什么:
# - 保留原始失败证据并生成确定性反馈 ID
# - 对证明失败、可选失败和未决失败使用配置化严重度语义
#
# 本脚本不做什么:
# - 不修改 Layout/Problem，不执行 hint，不选择释放对象或 Solver
# - 不启动重布局、跨项目流程或自动 Feedback Loop
#
# 制度边界声明:
# - suggested_action 始终是非强制提示，批次显式标记 hints_are_non_binding
# - UNRESOLVED 反馈不得升级为已证明的 BLOCKING/INFEASIBLE 结论
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: sc_tse_feedback_generator_v0001
# family: sc_tse_feedback_generator
# role: phase3_deterministic_layout_feedback_generation
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/sc_tse/sc_tse_feedback_generator_v0001.py
# input:
#   - validated SC-TSE Phase 1 or Phase 2 result
#   - validated Phase3Config
# output:
#   - SC-TSE LayoutFeedback Batch primitive mapping
# depends_on:
#   - Python stdlib: dataclasses, hashlib, json, typing
#   - sc_tse_feedback_contracts_v0001
# used_by:
#   - run_sc_tse_phase3_v0001
#   - test_sc_tse_phase3_v0001
# ============================================================

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from sc_tse_feedback_contracts_v0001 import Phase3Config


DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "sc_tse_feedback_generator"
SCRIPT_NAME = "sc_tse_feedback_generator_v0001"
SCRIPT_VERSION = "v0001"


# ============================================================
# 异常类型
# ============================================================

class ScTseFeedbackGenerationError(RuntimeError):
    """Base error for deterministic LayoutFeedback generation."""


class UnsupportedSourceResultError(ScTseFeedbackGenerationError):
    """Raised when a result is not a supported SC-TSE computation result."""


# ============================================================
# 数据结构
# ============================================================

@dataclass(frozen=True)
class FeedbackGenerationResult:
    feedback_batch: Mapping[str, Any]
    feedback_count: int
    evidence: tuple[Mapping[str, Any], ...]


# ============================================================
# 工具函数区
# ============================================================

def canonical_sha256(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode(DEFAULT_ENCODING)).hexdigest()


def _stable_feedback_id(
    problem_id: str,
    source: str,
    reason: str,
    affected_blocks: Sequence[str],
    affected_gates: Sequence[str],
    evidence: Mapping[str, Any],
) -> str:
    basis = {
        "problem_id": problem_id,
        "source": source,
        "reason": reason,
        "affected_blocks": sorted(set(affected_blocks)),
        "affected_gates": sorted(set(affected_gates)),
        "evidence": evidence,
    }
    return f"lfb-{canonical_sha256(basis)[:24]}"


def _feedback(
    problem_id: str,
    source: str,
    reason: str,
    affected_blocks: Sequence[str],
    affected_gates: Sequence[str],
    severity: str,
    evidence: Mapping[str, Any],
    suggested_action: str | None = None,
) -> dict[str, Any]:
    blocks = sorted(set(str(item) for item in affected_blocks))
    gates = sorted(set(str(item) for item in affected_gates))
    return {
        "feedback_id": _stable_feedback_id(problem_id, source, reason, blocks, gates, evidence),
        "source": source,
        "reason": reason,
        "affected_blocks": blocks,
        "affected_gates": gates,
        "severity": severity,
        "direction_hint": None,
        "magnitude_hint": None,
        "suggested_action": suggested_action,
        "evidence": dict(evidence),
    }


def _severity(source_result: Mapping[str, Any], required: bool, config: Phase3Config) -> str:
    if source_result["solve_status"] == "UNRESOLVED":
        return config.severity_policy["unresolved_failure"]
    if required:
        return config.severity_policy["proven_required_failure"]
    return config.severity_policy["optional_failure"]


def _layout_feedback(source_result: Mapping[str, Any], config: Phase3Config) -> list[dict[str, Any]]:
    if source_result["solve_status"] in {"FEASIBLE", "NOT_EVALUATED"}:
        return []
    problem_id = str(source_result["problem_id"])
    severity = _severity(source_result, True, config)
    feedback: list[dict[str, Any]] = []
    for evidence in source_result.get("evidence", []):
        if not isinstance(evidence, dict) or evidence.get("satisfied") is not False:
            continue
        constraint_id = str(evidence.get("constraint_id", "UNKNOWN"))
        feedback.append(
            _feedback(
                problem_id,
                "LAYOUT_SOLVER",
                "CONSTRAINT_NOT_SATISFIED",
                [str(item) for item in evidence.get("subjects", [])],
                [],
                severity,
                {"constraint_id": constraint_id, "constraint_status": dict(evidence)},
            )
        )
    if not feedback:
        termination = str(source_result["solver_metadata"].get("termination_reason") or "LAYOUT_NOT_RESOLVED")
        feedback.append(
            _feedback(
                problem_id,
                "LAYOUT_SOLVER",
                termination,
                [],
                [],
                severity,
                {
                    "diagnostics": dict(source_result.get("diagnostics", {})),
                    "solver_metadata": dict(source_result["solver_metadata"]),
                },
            )
        )
    return feedback


def _connectivity_feedback(source_result: Mapping[str, Any], config: Phase3Config) -> list[dict[str, Any]]:
    required_by_connection = {
        str(item["connection_id"]): bool(item["required"])
        for item in source_result.get("connection_results", [])
    }
    problem_id = str(source_result["problem_id"])
    feedback: list[dict[str, Any]] = []
    for failure in source_result.get("failure_data", []):
        connection_id = str(failure["connection_id"])
        required = required_by_connection.get(connection_id, True)
        evidence = {
            "connection_id": connection_id,
            "failure_reason": str(failure["reason"]),
            "failure_evidence": dict(failure.get("evidence", {})),
        }
        feedback.append(
            _feedback(
                problem_id,
                "CONNECTIVITY_EVALUATOR",
                str(failure["reason"]),
                [str(item) for item in failure.get("affected_block_ids", [])],
                [str(item) for item in failure.get("affected_gate_ids", [])],
                _severity(source_result, required, config),
                evidence,
                str(failure["hint"]) if failure.get("hint") else None,
            )
        )
    return feedback


# ============================================================
# 默认映射
# ============================================================

SOURCE_RESULT_VERSIONS = {
    "LAYOUT": "sc-tse-layout-result-v0.1.0",
    "CONNECTIVITY": "sc-tse-connectivity-result-v0.1.0",
}


# ============================================================
# 核心类
# ============================================================

class LayoutFeedbackGenerator:
    """Generate feedback data without consuming it as workflow instructions."""

    def __init__(self, config: Phase3Config) -> None:
        self.config = config

    def generate(self, problem_class: str, source_result: Mapping[str, Any]) -> FeedbackGenerationResult:
        expected_version = SOURCE_RESULT_VERSIONS.get(problem_class)
        if expected_version is None or source_result.get("schema_version") != expected_version:
            raise UnsupportedSourceResultError("source result version does not match the declared problem class")
        if not isinstance(source_result.get("problem_id"), str) or not source_result["problem_id"]:
            raise UnsupportedSourceResultError("source result must contain a non-empty problem_id")
        if problem_class == "LAYOUT":
            feedback = _layout_feedback(source_result, self.config)
        else:
            feedback = _connectivity_feedback(source_result, self.config)
        batch = {
            "schema_version": self.config.feedback_batch_schema_version,
            "source_problem_id": str(source_result["problem_id"]),
            "source_result_schema_version": str(source_result["schema_version"]),
            "hints_are_non_binding": True,
            "feedback": feedback,
        }
        evidence = tuple(
            {"evidence_type": "LAYOUT_FEEDBACK_GENERATED", "feedback_id": item["feedback_id"], "source": item["source"]}
            for item in feedback
        )
        return FeedbackGenerationResult(batch, len(feedback), evidence)


# ============================================================
# Schema / 契约辅助函数
# ============================================================

def generate_layout_feedback(
    problem_class: str,
    source_result: Mapping[str, Any],
    config: Phase3Config,
) -> FeedbackGenerationResult:
    return LayoutFeedbackGenerator(config).generate(problem_class, source_result)


# ============================================================
# CLI / main 接口区
# ============================================================

# Import-only feedback generator; public CLI is run_sc_tse_phase3_v0001.py.
