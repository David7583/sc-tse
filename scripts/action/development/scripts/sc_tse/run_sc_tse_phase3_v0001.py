# ============================================================
# 文件名: run_sc_tse_phase3_v0001.py
# 中文名: SC-TSE Phase 3 结构化反馈与再次求解入口脚本
# 版本号: v0001
#
# 主层级: action
# 层级: development / sc_tse / phase3_feedback_runner
# 脚本定位: 调用方指定 Problem Class 的单次计算、反馈生成与 previous_feedback 追溯入口
#
# 职责说明:
# - 对调用方提交的 Layout 或 Connectivity Problem 执行一次已有计算入口
# - 将当前计算失败转换为 LayoutFeedback，并记录可选 previous_feedback 上下文
#
# 本脚本做什么:
# - 支持调用方更新 Problem 后再次调用并获得新的计算结果与反馈批次
# - 严格输出 Execution、Solve、Quality、Diagnostics、Evidence 和零自动循环元数据
#
# 本脚本不做什么:
# - 不修改 Problem，不自动执行 suggested_action，不释放或移动对象
# - 不自主选择 Problem Class/Solver，不跨 Solver 重试，不拥有项目工作流
#
# 制度边界声明:
# - problem_class 由调用方显式指定；previous_feedback 仅 TRACE_ONLY
# - 单次调用最多执行一个源 Solver，不产生自动 Problem mutation 或 feedback cycle
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: run_sc_tse_phase3_v0001
# family: run_sc_tse_phase3
# role: phase3_structured_feedback_and_resolve_entry
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/sc_tse/run_sc_tse_phase3_v0001.py
# input:
#   - config/action/config/sc_tse_phase3_config_v0001.json
#   - caller-selected SC-TSE LayoutProblem or ConnectivityProblem JSON
#   - optional previous LayoutFeedback Batch JSON
# output:
#   - SC-TSE Phase 3 structured result JSON on stdout
# depends_on:
#   - Python stdlib: argparse, json, pathlib, sys, typing
#   - jsonschema
#   - run_sc_tse_phase1_v0001
#   - run_sc_tse_phase2_v0001
#   - sc_tse_feedback_contracts_v0001
#   - sc_tse_feedback_generator_v0001
# used_by:
#   - test_sc_tse_phase3_v0001
# ============================================================

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

import jsonschema


for _parent in Path(__file__).resolve().parents:
    if (_parent / "PROGRAMMING_LANGUAGE_SCRIPT_FORMAT.md").is_file():
        _bootstrap_project_root = _parent
        break
else:
    raise RuntimeError("cannot locate project root from script path")
_formal_module_root = _bootstrap_project_root / "scripts" / "action" / "development" / "scripts" / "sc_tse"
for _module_root in (Path(__file__).resolve().parent, _formal_module_root):
    if str(_module_root) not in sys.path:
        sys.path.insert(0, str(_module_root))

from run_sc_tse_phase1_v0001 import ScTsePhase1Error, run_phase1
from run_sc_tse_phase2_v0001 import ScTsePhase2Error, run_phase2
from sc_tse_connectivity_contracts_v0001 import ScTseConnectivityContractError
from sc_tse_connectivity_engine_v0001 import ScTseConnectivityEngineError
from sc_tse_constraint_evaluator_v0001 import ScTseConstraintEvaluationError
from sc_tse_feedback_contracts_v0001 import (
    PreviousFeedbackBatch,
    ScTseFeedbackContractError,
    load_phase3_config,
    load_previous_feedback,
)
from sc_tse_feedback_generator_v0001 import (
    ScTseFeedbackGenerationError,
    canonical_sha256,
    generate_layout_feedback,
)
from sc_tse_geometry_v0001 import ScTseGeometryError
from sc_tse_layout_contracts_v0001 import ScTseLayoutContractError


DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "run_sc_tse_phase3"
SCRIPT_NAME = "run_sc_tse_phase3_v0001"
SCRIPT_VERSION = "v0001"

DEFAULT_CONFIG_PATH = "config/action/config/sc_tse_phase3_config_v0001.json"
FEEDBACK_SCHEMA_FILENAME = "sc_tse_layout_feedback_batch_v0001.schema.json"
RESULT_SCHEMA_FILENAME = "sc_tse_phase3_result_v0001.schema.json"


# ============================================================
# 异常类型
# ============================================================

class ScTsePhase3Error(RuntimeError):
    """Base error for the Phase 3 public computation entry."""


class Phase3ResultContractError(ScTsePhase3Error):
    """Raised when an internally generated result violates a public schema."""


class Phase3PathError(ScTsePhase3Error):
    """Raised when a configured project-relative path escapes or is unavailable."""


# ============================================================
# 数据结构
# ============================================================

# Public results remain primitive JSON mappings to preserve a library-independent contract.


# ============================================================
# 工具函数区
# ============================================================

def _find_project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "PROGRAMMING_LANGUAGE_SCRIPT_FORMAT.md").is_file():
            return parent
    raise Phase3PathError("cannot locate project root from script path")


def _safe_json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _schema_path(filename: str) -> Path:
    return Path(__file__).resolve().parent / "schemas" / filename


def _read_schema(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding=DEFAULT_ENCODING))
    except (OSError, json.JSONDecodeError) as exc:
        raise Phase3ResultContractError(f"cannot read schema {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise Phase3ResultContractError(f"schema root must be an object: {path}")
    return value


def _validate_mapping(value: Mapping[str, Any], schema_path: Path, label: str) -> None:
    schema = _read_schema(schema_path)
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.Draft202012Validator(schema).validate(dict(value))
    except (jsonschema.SchemaError, jsonschema.ValidationError) as exc:
        raise Phase3ResultContractError(f"{label} violates contract: {exc.message}") from exc


def _resolve_project_path(project_root: Path, relative_path: str) -> Path:
    candidate = (project_root / relative_path).resolve()
    try:
        candidate.relative_to(project_root.resolve())
    except ValueError as exc:
        raise Phase3PathError("configured source solver path escapes project root") from exc
    if not candidate.is_file():
        raise Phase3PathError(f"configured source solver config not found: {relative_path}")
    return candidate


def _feedback_context(previous: PreviousFeedbackBatch | None) -> dict[str, Any]:
    identifiers = list(previous.feedback_ids) if previous is not None else []
    return {
        "previous_feedback_count": len(identifiers),
        "previous_feedback_ids": identifiers,
        "consumption_mode": "TRACE_ONLY",
        "automatic_action_applied": False,
    }


def _error_result(error: Exception, problem_class: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": "sc-tse-phase3-result-v0.1.0",
        "mode": "ERROR",
        "problem_class": problem_class,
        "problem_id": None,
        "execution_status": "ERROR",
        "solve_status": "NOT_EVALUATED",
        "solution_quality": "N/A",
        "source_result_schema_version": None,
        "source_result_sha256": None,
        "computational_result": None,
        "feedback_batch": None,
        "feedback_context": _feedback_context(None),
        "diagnostics": {"error_type": type(error).__name__, "detail": str(error)},
        "evidence": [],
        "solver_metadata": {
            "phase": "PHASE_3",
            "strategy": None,
            "source_solver_invocations": 0,
            "feedback_cycles": 0,
            "automatic_problem_mutations": 0,
            "termination_reason": "INPUT_OR_EXECUTION_ERROR",
        },
    }


# ============================================================
# 默认映射
# ============================================================

CLASSIFIED_INPUT_ERRORS = (
    ScTsePhase3Error,
    ScTseFeedbackContractError,
    ScTseFeedbackGenerationError,
    ScTsePhase1Error,
    ScTsePhase2Error,
    ScTseLayoutContractError,
    ScTseConnectivityContractError,
    ScTseConnectivityEngineError,
    ScTseConstraintEvaluationError,
    ScTseGeometryError,
    jsonschema.SchemaError,
)


# ============================================================
# 核心类
# ============================================================

class Phase3FeedbackKernel:
    """Run one caller-selected computation and generate non-orchestrating feedback."""

    def __init__(self, config_path: str | Path) -> None:
        self.project_root = _find_project_root()
        self.config = load_phase3_config(config_path)
        self.feedback_schema_path = _schema_path(FEEDBACK_SCHEMA_FILENAME)
        self.result_schema_path = _schema_path(RESULT_SCHEMA_FILENAME)

    def evaluate(
        self,
        problem_class: str,
        problem_path: str | Path,
        *,
        previous_feedback_path: str | Path | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        if problem_class not in self.config.supported_problem_classes:
            raise ScTsePhase3Error(f"unsupported problem_class: {problem_class}")
        previous = load_previous_feedback(
            previous_feedback_path,
            self.config,
            self.feedback_schema_path,
        )
        source_config = _resolve_project_path(
            self.project_root,
            self.config.source_solver_configs[problem_class],
        )
        if problem_class == "LAYOUT":
            source_result = run_phase1(source_config, problem_path, dry_run=dry_run)
        else:
            source_result = run_phase2(source_config, problem_path, dry_run=dry_run)
        generated = generate_layout_feedback(problem_class, source_result, self.config)
        _validate_mapping(generated.feedback_batch, self.feedback_schema_path, "feedback batch")
        result = {
            "schema_version": self.config.result_schema_version,
            "mode": "DRY_RUN" if dry_run else "RUN",
            "problem_class": problem_class,
            "problem_id": source_result["problem_id"],
            "execution_status": source_result["execution_status"],
            "solve_status": source_result["solve_status"],
            "solution_quality": source_result["solution_quality"],
            "source_result_schema_version": source_result["schema_version"],
            "source_result_sha256": canonical_sha256(source_result),
            "computational_result": source_result,
            "feedback_batch": dict(generated.feedback_batch),
            "feedback_context": _feedback_context(previous),
            "diagnostics": {
                "detail": "One caller-selected computation completed; feedback was generated without orchestration.",
                "generated_feedback_count": generated.feedback_count,
                "previous_feedback_source_problem_id": previous.source_problem_id if previous is not None else None,
                "feedback_generation_only": True,
            },
            "evidence": [dict(item) for item in generated.evidence],
            "solver_metadata": {
                "phase": "PHASE_3",
                "strategy": "STRUCTURED_FEEDBACK_FROM_COMPUTATION",
                "source_solver_invocations": self.config.max_source_solver_invocations_per_call,
                "feedback_cycles": self.config.max_feedback_cycles,
                "automatic_problem_mutations": self.config.max_automatic_problem_mutations,
                "termination_reason": "DRY_RUN_PRECHECK_COMPLETED" if dry_run else "SINGLE_COMPUTATION_COMPLETED",
            },
        }
        _validate_mapping(result, self.result_schema_path, "Phase 3 result")
        return result


# ============================================================
# Schema / 契约辅助函数
# ============================================================

def run_phase3(
    config_path: str | Path,
    problem_class: str,
    problem_path: str | Path,
    *,
    previous_feedback_path: str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    return Phase3FeedbackKernel(config_path).evaluate(
        problem_class,
        problem_path,
        previous_feedback_path=previous_feedback_path,
        dry_run=dry_run,
    )


# ============================================================
# CLI / main 接口区
# ============================================================

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one SC-TSE computation and generate structured LayoutFeedback.")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--problem-class", required=True, choices=["LAYOUT", "CONNECTIVITY"])
    parser.add_argument("--problem", required=True)
    parser.add_argument("--previous-feedback")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    result_schema = _schema_path(RESULT_SCHEMA_FILENAME)
    try:
        result = run_phase3(
            args.config,
            args.problem_class,
            args.problem,
            previous_feedback_path=args.previous_feedback,
            dry_run=bool(args.dry_run),
        )
        print(_safe_json_dumps(result))
        return 4 if result["execution_status"] == "INTERRUPTED" else 0
    except CLASSIFIED_INPUT_ERRORS as exc:
        result = _error_result(exc, args.problem_class)
        try:
            _validate_mapping(result, result_schema, "Phase 3 error result")
        except Phase3ResultContractError:
            pass
        print(_safe_json_dumps(result))
        return 2
    except Exception as exc:
        result = _error_result(exc, args.problem_class)
        print(_safe_json_dumps(result))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
