# ============================================================
# 文件名: run_sc_tse_phase0_v0001.py
# 中文名: SC-TSE Phase 0 计算入口脚本
# 版本号: v0001
#
# 主层级: action
# 层级: development / sc_tse / phase0_runner
# 脚本定位: 标准 Computational Problem 的 Phase 0 只读计算与结构化结果入口
#
# 职责说明:
# - 编排契约校验、几何校验和关系计算并返回三层结果语义
# - 提供无持久化副作用的 CLI 与 dry-run 验证入口
#
# 本脚本做什么:
# - 对标准化 Problem 生成 Geometry Validation、2D/2.5D 关系和 Evidence
# - 将已分类输入错误与未分类程序错误映射为稳定 JSON 和退出码
#
# 本脚本不做什么:
# - 不构造业务 Problem，不解释领域规则，不执行布局或连通求解
# - 不自动修复输入，不编排 Feedback/Re-layout，不修改任何项目或用户状态
#
# 制度边界声明:
# - stdout 只输出单行机器可读 JSON，输入与配置始终只读
# - Phase 0 不声称已执行求解，因此 Solve Status 固定为 NOT_EVALUATED
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: run_sc_tse_phase0_v0001
# family: run_sc_tse_phase0
# role: phase0_computational_kernel_entry
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/sc_tse/run_sc_tse_phase0_v0001.py
# input:
#   - config/action/config/sc_tse_phase0_config_v0001.json
#   - SC-TSE Computational Problem JSON
# output:
#   - SC-TSE Phase 0 structured result JSON on stdout
# depends_on:
#   - Python stdlib: argparse, json, pathlib, typing
#   - jsonschema
#   - sc_tse_contracts_v0001
#   - sc_tse_geometry_v0001
# used_by:
#   - test_sc_tse_phase0_v0001
# ============================================================

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

import jsonschema

from sc_tse_contracts_v0001 import (
    ComputationalProblemLoader,
    ScTseContractError,
    load_phase0_config,
)
from sc_tse_geometry_v0001 import (
    ScTseGeometryError,
    evaluate_block_relations,
    validate_problem_geometries,
)


DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "run_sc_tse_phase0"
SCRIPT_NAME = "run_sc_tse_phase0_v0001"
SCRIPT_VERSION = "v0001"

DEFAULT_CONFIG_PATH = "config/action/config/sc_tse_phase0_config_v0001.json"
PROBLEM_SCHEMA_FILENAME = "sc_tse_computational_problem_v0001.schema.json"
RESULT_SCHEMA_FILENAME = "sc_tse_phase0_result_v0001.schema.json"


# ============================================================
# 异常类型
# ============================================================

class ScTsePhase0Error(RuntimeError):
    """Base error for the Phase 0 public computation entry."""


class ResultContractError(ScTsePhase0Error):
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
        raise ResultContractError(f"cannot read result schema {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ResultContractError(f"schema root must be an object: {path}")
    return value


def _validate_result(result: Mapping[str, Any], schema_path: Path) -> None:
    schema = _read_schema(schema_path)
    try:
        jsonschema.Draft202012Validator(schema).validate(dict(result))
    except (jsonschema.SchemaError, jsonschema.ValidationError) as exc:
        raise ResultContractError(f"generated result violates result contract: {exc.message}") from exc


def _error_result(error: Exception, error_type: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": "sc-tse-phase0-result-v0.1.0",
        "mode": "ERROR",
        "problem_id": None,
        "execution_status": "ERROR",
        "solve_status": "NOT_EVALUATED",
        "solution_quality": "N/A",
        "geometry_validations": [],
        "geometry_relations": [],
        "diagnostics": {
            "error_type": error_type or type(error).__name__,
            "detail": str(error),
        },
        "evidence": [],
        "solver_metadata": {
            "strategy": None,
            "proof_capability": "NONE",
            "phase": "PHASE_0",
        },
    }


# ============================================================
# 默认映射
# ============================================================

CLASSIFIED_ERRORS = (ScTseContractError, ScTseGeometryError, ResultContractError)


# ============================================================
# 核心类
# ============================================================

class Phase0Kernel:
    """Execute the bounded Phase 0 computation without owning any workflow state."""

    def __init__(self, config_path: str | Path) -> None:
        self.config_path = Path(config_path)
        self.config = load_phase0_config(self.config_path)
        self.problem_schema_path = _schema_path(PROBLEM_SCHEMA_FILENAME)
        self.result_schema_path = _schema_path(RESULT_SCHEMA_FILENAME)
        self.loader = ComputationalProblemLoader(self.config, self.problem_schema_path)

    def evaluate(self, problem_path: str | Path, *, dry_run: bool = False) -> dict[str, Any]:
        problem = self.loader.load(problem_path)
        validations = validate_problem_geometries(problem.payload, self.config.tolerance)
        relations = evaluate_block_relations(problem.payload, self.config.tolerance)
        result = {
            "schema_version": self.config.result_schema_version,
            "mode": "DRY_RUN" if dry_run else "RUN",
            "problem_id": problem.problem_id,
            "execution_status": "COMPLETED",
            "solve_status": "NOT_EVALUATED",
            "solution_quality": "N/A",
            "geometry_validations": [item.to_dict() for item in validations],
            "geometry_relations": [item.to_dict() for item in relations],
            "diagnostics": {
                "validated_geometry_count": len(validations),
                "evaluated_relation_count": len(relations),
                "detail": "Phase 0 validates geometry facts; no layout solve was executed.",
            },
            "evidence": [
                {
                    "evidence_type": "CONTRACT_VERSION",
                    "value": self.config.problem_schema_version,
                },
                {
                    "evidence_type": "NUMERICAL_TOLERANCE",
                    "value": {
                        "linear_m": self.config.tolerance.linear_m,
                        "area_m2": self.config.tolerance.area_m2,
                        "z_m": self.config.tolerance.z_m,
                    },
                },
            ],
            "solver_metadata": {
                "strategy": None,
                "proof_capability": "NONE",
                "phase": "PHASE_0",
            },
        }
        _validate_result(result, self.result_schema_path)
        return result


# ============================================================
# Schema / 契约辅助函数
# ============================================================

def run_phase0(
    config_path: str | Path,
    problem_path: str | Path,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    return Phase0Kernel(config_path).evaluate(problem_path, dry_run=dry_run)


# ============================================================
# CLI / main 接口区
# ============================================================

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and evaluate an SC-TSE Phase 0 Computational Problem."
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--problem", required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    result_schema = _schema_path(RESULT_SCHEMA_FILENAME)
    try:
        result = run_phase0(args.config, args.problem, dry_run=bool(args.dry_run))
        print(_safe_json_dumps(result))
        return 0
    except CLASSIFIED_ERRORS as exc:
        result = _error_result(exc)
        try:
            _validate_result(result, result_schema)
        except ResultContractError:
            pass
        print(_safe_json_dumps(result))
        return 2
    except Exception as exc:
        result = _error_result(exc)
        print(_safe_json_dumps(result))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
