# ============================================================
# 文件名: run_sc_tse_phase2_v0001.py
# 中文名: SC-TSE Phase 2 基础连通性入口脚本
# 版本号: v0001
#
# 主层级: action
# 层级: development / sc_tse / phase2_connectivity_runner
# 脚本定位: 标准 ConnectivityProblem 的只读计算与结构化结果入口
#
# 职责说明:
# - 编排 Phase 2 契约、几何和基础连通性计算并校验公开结果
# - 提供无持久化副作用的 CLI 与 dry-run 预检查入口
#
# 本脚本做什么:
# - 输出严格分离的 Execution、Solve、Quality、Diagnostics 和 Evidence
# - 将当前策略失败表达为 UNRESOLVED 和结构化 Failure Data
#
# 本脚本不做什么:
# - 不修改 Problem/Layout，不决定释放对象、换 Solver 或继续循环
# - 不承担 Orchestrator、State Machine 或完整自主反馈闭环
#
# 制度边界声明:
# - stdout 只输出单行机器可读 JSON，输入和配置始终只读
# - exit code 表示执行层；当前离散图无路径不冒充通用 INFEASIBLE 证明
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: run_sc_tse_phase2_v0001
# family: run_sc_tse_phase2
# role: phase2_basic_connectivity_kernel_entry
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/sc_tse/run_sc_tse_phase2_v0001.py
# input:
#   - config/action/config/sc_tse_phase2_config_v0001.json
#   - SC-TSE ConnectivityProblem JSON
# output:
#   - SC-TSE Phase 2 structured connectivity result JSON on stdout
# depends_on:
#   - Python stdlib: argparse, json, pathlib, typing
#   - jsonschema
#   - Shapely 2.x
#   - sc_tse_geometry_v0001
#   - sc_tse_connectivity_contracts_v0001
#   - sc_tse_connectivity_engine_v0001
# used_by:
#   - test_sc_tse_phase2_v0001
# ============================================================

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

import jsonschema
from shapely.geometry import Point

from sc_tse_connectivity_contracts_v0001 import (
    ConnectivityProblemLoader,
    ScTseConnectivityContractError,
    load_phase2_config,
)
from sc_tse_connectivity_engine_v0001 import (
    ConnectivityEvaluator,
    ConnectivityGeometryError,
    evaluate_connectivity,
)
from sc_tse_geometry_v0001 import ScTseGeometryError, validate_problem_geometries


DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "run_sc_tse_phase2"
SCRIPT_NAME = "run_sc_tse_phase2_v0001"
SCRIPT_VERSION = "v0001"

DEFAULT_CONFIG_PATH = "config/action/config/sc_tse_phase2_config_v0001.json"
PROBLEM_SCHEMA_FILENAME = "sc_tse_connectivity_problem_v0001.schema.json"
RESULT_SCHEMA_FILENAME = "sc_tse_connectivity_result_v0001.schema.json"


# ============================================================
# 异常类型
# ============================================================

class ScTsePhase2Error(RuntimeError):
    """Base error for the Phase 2 public computation entry."""


class ConnectivityResultContractError(ScTsePhase2Error):
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
        raise ConnectivityResultContractError(f"cannot read result schema {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConnectivityResultContractError(f"schema root must be an object: {path}")
    return value


def _validate_result(result: Mapping[str, Any], schema_path: Path) -> None:
    schema = _read_schema(schema_path)
    try:
        jsonschema.Draft202012Validator(schema).validate(dict(result))
    except (jsonschema.SchemaError, jsonschema.ValidationError) as exc:
        raise ConnectivityResultContractError(
            f"generated result violates result contract: {exc.message}"
        ) from exc


def _error_result(error: Exception) -> dict[str, Any]:
    return {
        "schema_version": "sc-tse-connectivity-result-v0.1.0",
        "mode": "ERROR",
        "problem_id": None,
        "execution_status": "ERROR",
        "solve_status": "NOT_EVALUATED",
        "solution_quality": "N/A",
        "connectivity_status": "NOT_EVALUATED",
        "connection_results": [],
        "topology": {},
        "failure_data": [],
        "diagnostics": {"error_type": type(error).__name__, "detail": str(error)},
        "evidence": [],
        "solver_metadata": {
            "phase": "PHASE_2",
            "path_strategy": None,
            "proof_capability": "NONE",
            "termination_reason": "INPUT_OR_EXECUTION_ERROR",
            "grid_expansions": 0,
        },
    }


# ============================================================
# 默认映射
# ============================================================

CLASSIFIED_INPUT_ERRORS = (
    ScTseConnectivityContractError,
    ScTseGeometryError,
    ConnectivityGeometryError,
    ConnectivityResultContractError,
    jsonschema.SchemaError,
)


# ============================================================
# 核心类
# ============================================================

class Phase2ConnectivityKernel:
    """Execute bounded Phase 2 connectivity computation without workflow ownership."""

    def __init__(self, config_path: str | Path) -> None:
        self.config_path = Path(config_path)
        self.config = load_phase2_config(self.config_path)
        self.problem_schema_path = _schema_path(PROBLEM_SCHEMA_FILENAME)
        self.result_schema_path = _schema_path(RESULT_SCHEMA_FILENAME)
        self.loader = ConnectivityProblemLoader(self.config, self.problem_schema_path)

    def evaluate(self, problem_path: str | Path, *, dry_run: bool = False) -> dict[str, Any]:
        problem = self.loader.load(problem_path)
        if dry_run:
            result = self._dry_run(problem)
        else:
            outcome = evaluate_connectivity(problem, self.config)
            result = {
                "schema_version": self.config.result_schema_version,
                "mode": "RUN",
                "problem_id": problem.problem_id,
                "execution_status": outcome.execution_status,
                "solve_status": outcome.solve_status,
                "solution_quality": outcome.solution_quality,
                "connectivity_status": outcome.connectivity_status,
                "connection_results": [dict(item) for item in outcome.connection_results],
                "topology": dict(outcome.topology),
                "failure_data": [dict(item) for item in outcome.failure_data],
                "diagnostics": dict(outcome.diagnostics),
                "evidence": [dict(item) for item in outcome.evidence],
                "solver_metadata": {
                    "phase": "PHASE_2",
                    "path_strategy": "GRID_A_STAR_4_NEIGHBOR",
                    "proof_capability": outcome.proof_capability,
                    "termination_reason": outcome.termination_reason,
                    "grid_expansions": outcome.grid_expansions,
                },
            }
        _validate_result(result, self.result_schema_path)
        return result

    def _dry_run(self, problem: Any) -> dict[str, Any]:
        validations = validate_problem_geometries(problem.payload, self.config.tolerance)
        evaluator = ConnectivityEvaluator(self.config)
        site, block_shapes, free_space = evaluator._free_space(problem)
        for gate_id, gate in problem.gates.items():
            owner = problem.gate_owners[gate_id]
            if block_shapes[owner].boundary.distance(Point(gate["position"])) > self.config.tolerance.linear_m:
                raise ConnectivityGeometryError(f"{gate_id}: Gate must lie on its owning Block boundary")
        return {
            "schema_version": self.config.result_schema_version,
            "mode": "DRY_RUN",
            "problem_id": problem.problem_id,
            "execution_status": "COMPLETED",
            "solve_status": "NOT_EVALUATED",
            "solution_quality": "N/A",
            "connectivity_status": "NOT_EVALUATED",
            "connection_results": [],
            "topology": {},
            "failure_data": [],
            "diagnostics": {
                "detail": "Contract, geometry, Gate, Buffer, and capacity prechecks completed; no path search ran.",
                "validated_geometry_count": len(validations),
                "validated_gate_count": len(problem.gates),
                "free_space_area_m2": round(float(free_space.area), 12),
                "site_area_m2": round(float(site.area), 12),
            },
            "evidence": [],
            "solver_metadata": {
                "phase": "PHASE_2",
                "path_strategy": "GRID_A_STAR_4_NEIGHBOR",
                "proof_capability": "NONE",
                "termination_reason": "DRY_RUN_PRECHECK_COMPLETED",
                "grid_expansions": 0,
            },
        }


# ============================================================
# Schema / 契约辅助函数
# ============================================================

def run_phase2(
    config_path: str | Path,
    problem_path: str | Path,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    return Phase2ConnectivityKernel(config_path).evaluate(problem_path, dry_run=dry_run)


# ============================================================
# CLI / main 接口区
# ============================================================

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate bounded SC-TSE Phase 2 connectivity.")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--problem", required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    result_schema = _schema_path(RESULT_SCHEMA_FILENAME)
    try:
        result = run_phase2(args.config, args.problem, dry_run=bool(args.dry_run))
        print(_safe_json_dumps(result))
        return 4 if result["execution_status"] == "INTERRUPTED" else 0
    except CLASSIFIED_INPUT_ERRORS as exc:
        result = _error_result(exc)
        try:
            _validate_result(result, result_schema)
        except ConnectivityResultContractError:
            pass
        print(_safe_json_dumps(result))
        return 2
    except Exception as exc:
        result = _error_result(exc)
        print(_safe_json_dumps(result))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
