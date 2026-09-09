# ============================================================
# 文件名: run_sc_tse_phase1_v0001.py
# 中文名: SC-TSE Phase 1 最小布局入口脚本
# 版本号: v0001
#
# 主层级: action
# 层级: development / sc_tse / phase1_runner
# 脚本定位: 标准 LayoutProblem 的 Phase 1 基础布局计算与结构化结果入口
#
# 职责说明:
# - 编排契约、几何、约束评估和 Basic Solver 并返回三层结果语义
# - 提供无持久化副作用的 CLI 与 dry-run 预检查入口
#
# 本脚本做什么:
# - 对显式有限候选域执行最小二维布局并输出 Diagnostics / Evidence
# - 将输入错误、能力不足、预算中止和未预期错误映射为稳定结果
#
# 本脚本不做什么:
# - 不构造业务 Problem，不解释领域规则，不决定锁定或释放对象
# - 不执行连接/路径求解、跨 Solver 调度或项目级自动反馈闭环
#
# 制度边界声明:
# - stdout 只输出单行机器可读 JSON，输入和配置始终只读
# - exit code 表示执行层；Solve Status 单独表达空间问题结论
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: run_sc_tse_phase1_v0001
# family: run_sc_tse_phase1
# role: phase1_minimal_layout_kernel_entry
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/sc_tse/run_sc_tse_phase1_v0001.py
# input:
#   - config/action/config/sc_tse_phase1_config_v0001.json
#   - SC-TSE LayoutProblem JSON
# output:
#   - SC-TSE Phase 1 structured layout result JSON on stdout
# depends_on:
#   - Python stdlib: argparse, json, pathlib, typing
#   - jsonschema
#   - sc_tse_geometry_v0001
#   - sc_tse_layout_contracts_v0001
#   - sc_tse_constraint_evaluator_v0001
#   - sc_tse_basic_layout_solver_v0001
# used_by:
#   - test_sc_tse_phase1_v0001
# ============================================================

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

import jsonschema

from sc_tse_basic_layout_solver_v0001 import solve_layout
from sc_tse_constraint_evaluator_v0001 import (
    ConstraintEvaluator,
    ConstraintParameterError,
    ScTseConstraintEvaluationError,
    SolverCapabilityError,
)
from sc_tse_layout_contracts_v0001 import (
    LayoutProblemLoader,
    ScTseLayoutContractError,
    load_phase1_config,
)


DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "run_sc_tse_phase1"
SCRIPT_NAME = "run_sc_tse_phase1_v0001"
SCRIPT_VERSION = "v0001"

DEFAULT_CONFIG_PATH = "config/action/config/sc_tse_phase1_config_v0001.json"
PROBLEM_SCHEMA_FILENAME = "sc_tse_layout_problem_v0001.schema.json"
RESULT_SCHEMA_FILENAME = "sc_tse_layout_result_v0001.schema.json"


# ============================================================
# Phase 0 几何异常导入
# ============================================================

from sc_tse_geometry_v0001 import (  # noqa: E402
    ScTseGeometryError,
    validate_problem_geometries,
)


# ============================================================
# 异常类型
# ============================================================

class ScTsePhase1Error(RuntimeError):
    """Base error for the Phase 1 public computation entry."""


class LayoutResultContractError(ScTsePhase1Error):
    """Raised when an internally generated result violates its public schema."""


# ============================================================
# 数据结构
# ============================================================

# Public results remain primitive JSON mappings to avoid leaking implementation types.


# ============================================================
# 工具函数区
# ============================================================

def _safe_json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _schema_path(filename: str) -> Path:
    return Path(__file__).resolve().parent / "schemas" / filename


def _read_schema(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding=DEFAULT_ENCODING))
    except (OSError, json.JSONDecodeError) as exc:
        raise LayoutResultContractError(f"cannot read result schema {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise LayoutResultContractError(f"schema root must be an object: {path}")
    return value


def _validate_result(result: Mapping[str, Any], schema_path: Path) -> None:
    schema = _read_schema(schema_path)
    try:
        jsonschema.Draft202012Validator(schema).validate(dict(result))
    except (jsonschema.SchemaError, jsonschema.ValidationError) as exc:
        raise LayoutResultContractError(
            f"generated result violates result contract: {exc.message}"
        ) from exc


def _error_result(error: Exception, error_type: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": "sc-tse-layout-result-v0.1.0",
        "mode": "ERROR",
        "problem_id": None,
        "execution_status": "ERROR",
        "solve_status": "NOT_EVALUATED",
        "solution_quality": "N/A",
        "layout_candidate": None,
        "diagnostics": {
            "error_type": error_type or type(error).__name__,
            "detail": str(error),
        },
        "evidence": [],
        "solver_metadata": {
            "phase": "PHASE_1",
            "strategy": None,
            "proof_capability": "NONE",
            "termination_reason": "INPUT_OR_EXECUTION_ERROR",
            "candidate_evaluations": 0,
            "search_exhausted": False,
        },
    }


def _capability_result(problem_id: str, error: SolverCapabilityError) -> dict[str, Any]:
    return {
        "schema_version": "sc-tse-layout-result-v0.1.0",
        "mode": "RUN",
        "problem_id": problem_id,
        "execution_status": "INTERRUPTED",
        "solve_status": "UNRESOLVED",
        "solution_quality": "UNKNOWN",
        "layout_candidate": None,
        "diagnostics": {
            "error_type": type(error).__name__,
            "detail": str(error),
        },
        "evidence": [],
        "solver_metadata": {
            "phase": "PHASE_1",
            "strategy": "DETERMINISTIC_GRID_SCAN",
            "proof_capability": "NONE",
            "termination_reason": "UNSUPPORTED_CONSTRAINT_TYPE",
            "candidate_evaluations": 0,
            "search_exhausted": False,
        },
    }


# ============================================================
# 默认映射
# ============================================================

CLASSIFIED_INPUT_ERRORS = (
    ScTseLayoutContractError,
    ScTseGeometryError,
    ConstraintParameterError,
    LayoutResultContractError,
)


# ============================================================
# 核心类
# ============================================================

class Phase1LayoutKernel:
    """Execute bounded Phase 1 layout computation without owning workflow state."""

    def __init__(self, config_path: str | Path) -> None:
        self.config_path = Path(config_path)
        self.config = load_phase1_config(self.config_path)
        self.problem_schema_path = _schema_path(PROBLEM_SCHEMA_FILENAME)
        self.result_schema_path = _schema_path(RESULT_SCHEMA_FILENAME)
        self.loader = LayoutProblemLoader(self.config, self.problem_schema_path)

    def evaluate(self, problem_path: str | Path, *, dry_run: bool = False) -> dict[str, Any]:
        problem = self.loader.load(problem_path)
        try:
            if dry_run:
                return self._dry_run(problem)
            outcome = solve_layout(problem, self.config)
            result = {
                "schema_version": self.config.result_schema_version,
                "mode": "RUN",
                "problem_id": problem.problem_id,
                "execution_status": outcome.execution_status,
                "solve_status": outcome.solve_status,
                "solution_quality": outcome.solution_quality,
                "layout_candidate": outcome.layout_candidate,
                "diagnostics": dict(outcome.diagnostics),
                "evidence": [dict(item) for item in outcome.evidence],
                "solver_metadata": {
                    "phase": "PHASE_1",
                    "strategy": "DETERMINISTIC_GRID_SCAN",
                    "proof_capability": outcome.proof_capability,
                    "termination_reason": outcome.termination_reason,
                    "candidate_evaluations": outcome.candidate_evaluations,
                    "search_exhausted": outcome.search_exhausted,
                },
            }
        except SolverCapabilityError as exc:
            result = _capability_result(problem.problem_id, exc)
        _validate_result(result, self.result_schema_path)
        return result

    def _dry_run(self, problem: Any) -> dict[str, Any]:
        validations = validate_problem_geometries(problem.payload, self.config.tolerance)
        blocks = {str(item["block_id"]): item for item in problem.payload["blocks"]}
        evaluator = ConstraintEvaluator(self.config)
        statuses = evaluator.evaluate_available(
            problem.payload["site"],
            blocks,
            problem.payload["constraints"],
        )
        result = {
            "schema_version": self.config.result_schema_version,
            "mode": "DRY_RUN",
            "problem_id": problem.problem_id,
            "execution_status": "COMPLETED",
            "solve_status": "NOT_EVALUATED",
            "solution_quality": "N/A",
            "layout_candidate": None,
            "diagnostics": {
                "detail": "Contract, geometry, capability, and constraint prechecks completed; no solve ran.",
                "validated_geometry_count": len(validations),
                "prechecked_constraint_count": len(statuses),
            },
            "evidence": [item.to_dict() for item in statuses],
            "solver_metadata": {
                "phase": "PHASE_1",
                "strategy": "DETERMINISTIC_GRID_SCAN",
                "proof_capability": "NONE",
                "termination_reason": "DRY_RUN_PRECHECK_COMPLETED",
                "candidate_evaluations": 0,
                "search_exhausted": False,
            },
        }
        _validate_result(result, self.result_schema_path)
        return result


# ============================================================
# Schema / 契约辅助函数
# ============================================================

def run_phase1(
    config_path: str | Path,
    problem_path: str | Path,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    return Phase1LayoutKernel(config_path).evaluate(problem_path, dry_run=dry_run)


# ============================================================
# CLI / main 接口区
# ============================================================

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Solve a bounded SC-TSE Phase 1 LayoutProblem."
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--problem", required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    result_schema = _schema_path(RESULT_SCHEMA_FILENAME)
    try:
        result = run_phase1(args.config, args.problem, dry_run=bool(args.dry_run))
        print(_safe_json_dumps(result))
        if result["execution_status"] == "INTERRUPTED":
            return 4
        return 0
    except CLASSIFIED_INPUT_ERRORS as exc:
        result = _error_result(exc)
        try:
            _validate_result(result, result_schema)
        except LayoutResultContractError:
            pass
        print(_safe_json_dumps(result))
        return 2
    except ScTseConstraintEvaluationError as exc:
        result = _error_result(exc)
        print(_safe_json_dumps(result))
        return 2
    except Exception as exc:
        result = _error_result(exc)
        print(_safe_json_dumps(result))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
