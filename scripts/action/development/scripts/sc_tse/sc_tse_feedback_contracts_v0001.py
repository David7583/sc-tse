# ============================================================
# 文件名: sc_tse_feedback_contracts_v0001.py
# 中文名: SC-TSE Phase 3 结构化反馈契约脚本
# 版本号: v0001
#
# 主层级: action
# 层级: development / sc_tse / phase3_feedback_contracts
# 脚本定位: LayoutFeedback Batch、previous_feedback 与 Phase 3 配置的只读契约边界
#
# 职责说明:
# - 严格校验 Phase 3 配置和可选 previous_feedback 批次
# - 保持反馈身份、来源结果版本和非强制 hint 语义稳定
#
# 本脚本做什么:
# - 执行版本、Schema、容量、唯一 ID 和相对配置路径检查
# - 返回不携带第三方库私有类型的不可变配置与反馈批次
#
# 本脚本不做什么:
# - 不修改 Computational Problem，不消费 hint 执行业务动作
# - 不选择下一轮 Solver，不编排反馈循环或项目状态
#
# 制度边界声明:
# - previous_feedback 只作为调用方提供的追溯上下文，不取得决策权
# - 配置路径必须保持在项目根目录内，输入文件始终只读
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: sc_tse_feedback_contracts_v0001
# family: sc_tse_feedback_contracts
# role: phase3_layout_feedback_contract_gateway
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/sc_tse/sc_tse_feedback_contracts_v0001.py
# input:
#   - config/action/config/sc_tse_phase3_config_v0001.json
#   - optional SC-TSE LayoutFeedback Batch JSON
# output:
#   - validated Phase3Config and PreviousFeedbackBatch
# depends_on:
#   - Python stdlib: dataclasses, json, pathlib, typing
#   - jsonschema
# used_by:
#   - sc_tse_feedback_generator_v0001
#   - run_sc_tse_phase3_v0001
#   - test_sc_tse_phase3_v0001
# ============================================================

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import jsonschema


DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "sc_tse_feedback_contracts"
SCRIPT_NAME = "sc_tse_feedback_contracts_v0001"
SCRIPT_VERSION = "v0001"

CONFIG_VERSION = "sc-tse-phase3-config-v0.1.0"
FEEDBACK_BATCH_SCHEMA_VERSION = "sc-tse-layout-feedback-batch-v0.1.0"
PHASE3_RESULT_SCHEMA_VERSION = "sc-tse-phase3-result-v0.1.0"


# ============================================================
# 异常类型
# ============================================================

class ScTseFeedbackContractError(RuntimeError):
    """Base error for Phase 3 feedback contract failures."""


class FeedbackConfigError(ScTseFeedbackContractError):
    """Raised when the Phase 3 configuration is invalid."""


class PreviousFeedbackError(ScTseFeedbackContractError):
    """Raised when previous_feedback is invalid or exceeds configured limits."""


# ============================================================
# 数据结构
# ============================================================

@dataclass(frozen=True)
class Phase3Config:
    result_schema_version: str
    feedback_batch_schema_version: str
    supported_problem_classes: tuple[str, ...]
    source_solver_configs: Mapping[str, str]
    severity_policy: Mapping[str, str]
    max_previous_feedback_items: int
    max_feedback_file_bytes: int
    max_source_solver_invocations_per_call: int
    max_feedback_cycles: int
    max_automatic_problem_mutations: int


@dataclass(frozen=True)
class PreviousFeedbackBatch:
    source_problem_id: str
    feedback_ids: tuple[str, ...]
    payload: Mapping[str, Any]


# ============================================================
# 工具函数区
# ============================================================

def _read_mapping(path: str | Path, error_type: type[ScTseFeedbackContractError], max_bytes: int | None = None) -> dict[str, Any]:
    target = Path(path)
    try:
        if max_bytes is not None and target.stat().st_size > max_bytes:
            raise error_type(f"JSON file exceeds configured byte limit: {target}")
        value = json.loads(target.read_text(encoding=DEFAULT_ENCODING))
    except ScTseFeedbackContractError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise error_type(f"cannot read JSON object from {target}: {exc}") from exc
    if not isinstance(value, dict):
        raise error_type("JSON root must be an object")
    return value


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise FeedbackConfigError(f"{name} must be a positive integer")
    return value


def _non_negative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FeedbackConfigError(f"{name} must be a non-negative integer")
    return value


def _string_array(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
        raise FeedbackConfigError(f"{name} must be a non-empty string array")
    if len(set(value)) != len(value):
        raise FeedbackConfigError(f"{name} must not contain duplicates")
    return tuple(value)


def _strict_mapping(value: Any, fields: set[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise FeedbackConfigError(f"{name} fields do not match the versioned contract")
    return value


# ============================================================
# 默认映射
# ============================================================

EXPECTED_PROBLEM_CLASSES = {"LAYOUT", "CONNECTIVITY"}


# ============================================================
# 核心类
# ============================================================

class PreviousFeedbackLoader:
    """Load a previous feedback batch for trace-only Phase 3 context."""

    def __init__(self, config: Phase3Config, schema_path: str | Path) -> None:
        self.config = config
        schema = _read_mapping(schema_path, PreviousFeedbackError)
        try:
            jsonschema.Draft202012Validator.check_schema(schema)
        except jsonschema.SchemaError as exc:
            raise PreviousFeedbackError(f"invalid feedback batch schema: {exc.message}") from exc
        self.validator = jsonschema.Draft202012Validator(schema)

    def load(self, path: str | Path) -> PreviousFeedbackBatch:
        payload = _read_mapping(path, PreviousFeedbackError, self.config.max_feedback_file_bytes)
        errors = sorted(self.validator.iter_errors(payload), key=lambda item: list(item.absolute_path))
        if errors:
            location = ".".join(str(part) for part in errors[0].absolute_path) or "$"
            raise PreviousFeedbackError(f"{location}: {errors[0].message}")
        feedback = payload["feedback"]
        if len(feedback) > self.config.max_previous_feedback_items:
            raise PreviousFeedbackError("previous_feedback exceeds configured item limit")
        feedback_ids = tuple(str(item["feedback_id"]) for item in feedback)
        if len(set(feedback_ids)) != len(feedback_ids):
            raise PreviousFeedbackError("previous_feedback contains duplicate feedback_id values")
        return PreviousFeedbackBatch(str(payload["source_problem_id"]), feedback_ids, payload)


# ============================================================
# Schema / 契约辅助函数
# ============================================================

def load_phase3_config(path: str | Path) -> Phase3Config:
    raw = _read_mapping(path, FeedbackConfigError)
    _strict_mapping(
        raw,
        {"config_version", "contract_versions", "supported_problem_classes", "source_solver_configs", "severity_policy", "runtime"},
        "Phase 3 config",
    )
    if raw["config_version"] != CONFIG_VERSION:
        raise FeedbackConfigError("Phase 3 config version mismatch")
    versions = _strict_mapping(raw["contract_versions"], {"layout_feedback_batch", "phase3_result"}, "contract_versions")
    if versions["layout_feedback_batch"] != FEEDBACK_BATCH_SCHEMA_VERSION or versions["phase3_result"] != PHASE3_RESULT_SCHEMA_VERSION:
        raise FeedbackConfigError("Phase 3 contract version mismatch")
    problem_classes = _string_array(raw["supported_problem_classes"], "supported_problem_classes")
    if set(problem_classes) != EXPECTED_PROBLEM_CLASSES:
        raise FeedbackConfigError("supported_problem_classes must match the implemented Phase 3 capability")
    source_configs = _strict_mapping(raw["source_solver_configs"], EXPECTED_PROBLEM_CLASSES, "source_solver_configs")
    if any(not isinstance(value, str) or not value or Path(value).is_absolute() for value in source_configs.values()):
        raise FeedbackConfigError("source_solver_configs must contain non-empty project-relative paths")
    severity = _strict_mapping(
        raw["severity_policy"],
        {"proven_required_failure", "optional_failure", "unresolved_failure"},
        "severity_policy",
    )
    if dict(severity) != {
        "proven_required_failure": "BLOCKING",
        "optional_failure": "NON_BLOCKING",
        "unresolved_failure": "INDETERMINATE",
    }:
        raise FeedbackConfigError("severity_policy must preserve the three Phase 3 severity meanings")
    runtime = _strict_mapping(
        raw["runtime"],
        {
            "max_previous_feedback_items",
            "max_feedback_file_bytes",
            "max_source_solver_invocations_per_call",
            "max_feedback_cycles",
            "max_automatic_problem_mutations",
        },
        "runtime",
    )
    source_invocations = _positive_int(runtime["max_source_solver_invocations_per_call"], "max_source_solver_invocations_per_call")
    feedback_cycles = _non_negative_int(runtime["max_feedback_cycles"], "max_feedback_cycles")
    automatic_mutations = _non_negative_int(runtime["max_automatic_problem_mutations"], "max_automatic_problem_mutations")
    if source_invocations != 1 or feedback_cycles != 0 or automatic_mutations != 0:
        raise FeedbackConfigError("Phase 3 Core MVP permits one source solve and no automatic feedback cycles or problem mutations")
    return Phase3Config(
        result_schema_version=PHASE3_RESULT_SCHEMA_VERSION,
        feedback_batch_schema_version=FEEDBACK_BATCH_SCHEMA_VERSION,
        supported_problem_classes=problem_classes,
        source_solver_configs={str(key): str(value) for key, value in source_configs.items()},
        severity_policy={str(key): str(value) for key, value in severity.items()},
        max_previous_feedback_items=_positive_int(runtime["max_previous_feedback_items"], "max_previous_feedback_items"),
        max_feedback_file_bytes=_positive_int(runtime["max_feedback_file_bytes"], "max_feedback_file_bytes"),
        max_source_solver_invocations_per_call=source_invocations,
        max_feedback_cycles=feedback_cycles,
        max_automatic_problem_mutations=automatic_mutations,
    )


def load_previous_feedback(
    path: str | Path | None,
    config: Phase3Config,
    schema_path: str | Path,
) -> PreviousFeedbackBatch | None:
    if path is None:
        return None
    return PreviousFeedbackLoader(config, schema_path).load(path)


# ============================================================
# CLI / main 接口区
# ============================================================

# Import-only contract gateway; public CLI is run_sc_tse_phase3_v0001.py.
