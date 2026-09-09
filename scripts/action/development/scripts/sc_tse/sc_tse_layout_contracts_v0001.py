# ============================================================
# 文件名: sc_tse_layout_contracts_v0001.py
# 中文名: SC-TSE Phase 1 布局问题契约脚本
# 版本号: v0001
#
# 主层级: action
# 层级: development / sc_tse / phase1_layout_contracts
# 脚本定位: Phase 1 LayoutProblem、配置和变量自由度的只读校验边界
#
# 职责说明:
# - 读取并严格校验版本化 LayoutProblem 与 Phase 1 配置
# - 校验对象身份、Constraint 引用和 Position FIXED/FREE 控制
#
# 本脚本做什么:
# - 使用 JSON Schema 校验结构并执行跨对象引用和能力预算检查
# - 返回不携带第三方库类型的不可变配置与 LayoutProblem
#
# 本脚本不做什么:
# - 不解释 block_type、领域规范、项目规则或用户为何锁定对象
# - 不执行几何计算、布局搜索、连接求解、反馈编排或持久化
#
# 制度边界声明:
# - 每个体块必须显式给出一个 position FIXED/FREE 控制，禁止猜测
# - Phase 1 不接受 Gate、ConnectionRequirement、LIMITED 或 SOFT_LOCK
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: sc_tse_layout_contracts_v0001
# family: sc_tse_layout_contracts
# role: phase1_layout_problem_contract_gateway
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/sc_tse/sc_tse_layout_contracts_v0001.py
# input:
#   - config/action/config/sc_tse_phase1_config_v0001.json
#   - SC-TSE LayoutProblem JSON
#   - sc_tse_layout_problem_v0001.schema.json
# output:
#   - validated Phase1Config and LayoutProblem
# depends_on:
#   - Python stdlib: dataclasses, json, math, pathlib, sys, typing
#   - jsonschema
#   - sc_tse_contracts_v0001
# used_by:
#   - sc_tse_constraint_evaluator_v0001
#   - sc_tse_basic_layout_solver_v0001
#   - run_sc_tse_phase1_v0001
#   - test_sc_tse_phase1_v0001
# ============================================================

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import jsonschema


DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "sc_tse_layout_contracts"
SCRIPT_NAME = "sc_tse_layout_contracts_v0001"
SCRIPT_VERSION = "v0001"

CONFIG_VERSION = "sc-tse-phase1-config-v0.1.0"
PROBLEM_SCHEMA_VERSION = "sc-tse-layout-problem-v0.1.0"
RESULT_SCHEMA_VERSION = "sc-tse-layout-result-v0.1.0"
INTERNAL_FRAME_ID = "SC_TSE_CARTESIAN_RH"
INTERNAL_LENGTH_UNIT = "m"


# ============================================================
# Phase 0 基础契约导入
# ============================================================

def _find_project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "PROGRAMMING_LANGUAGE_SCRIPT_FORMAT.md").is_file():
            return parent
    raise RuntimeError("cannot locate project root from script path")


PROJECT_ROOT = _find_project_root()
CURRENT_MODULE_ROOT = Path(__file__).resolve().parent
FORMAL_MODULE_ROOT = PROJECT_ROOT / "scripts" / "action" / "development" / "scripts" / "sc_tse"
PHASE0_MODULE_ROOT = (
    CURRENT_MODULE_ROOT
    if (CURRENT_MODULE_ROOT / "sc_tse_contracts_v0001.py").is_file()
    else FORMAL_MODULE_ROOT
)
for module_root in (PROJECT_ROOT, PHASE0_MODULE_ROOT):
    if str(module_root) not in sys.path:
        sys.path.insert(0, str(module_root))

from sc_tse_contracts_v0001 import (  # noqa: E402
    NumericalTolerance,
)


# ============================================================
# 异常类型
# ============================================================

class ScTseLayoutContractError(RuntimeError):
    """Base error for Phase 1 layout contract failures."""


class LayoutConfigError(ScTseLayoutContractError):
    """Raised when the versioned Phase 1 configuration is invalid."""


class LayoutInputReadError(ScTseLayoutContractError):
    """Raised when a JSON input cannot be read as a mapping."""


class LayoutInputSchemaError(ScTseLayoutContractError):
    """Raised when a LayoutProblem violates its JSON Schema."""


class LayoutInputReferenceError(ScTseLayoutContractError):
    """Raised when identities, references, or variable controls conflict."""


# ============================================================
# 数据结构
# ============================================================

@dataclass(frozen=True)
class Phase1Config:
    config_version: str
    problem_schema_version: str
    result_schema_version: str
    frame_id: str
    handedness: str
    z_axis: str
    length_unit: str
    tolerance: NumericalTolerance
    allowed_strategies: tuple[str, ...]
    supported_constraint_types: tuple[str, ...]
    supported_position_modes: tuple[str, ...]
    max_candidate_evaluations_limit: int
    max_problem_bytes: int


@dataclass(frozen=True)
class LayoutProblem:
    schema_version: str
    problem_id: str
    payload: Mapping[str, Any]
    position_modes: Mapping[str, str]


# ============================================================
# 工具函数区
# ============================================================

def _read_json_mapping(path: str | Path, *, max_bytes: int | None = None) -> dict[str, Any]:
    target = Path(path)
    try:
        if max_bytes is not None and target.stat().st_size > max_bytes:
            raise LayoutInputReadError(f"JSON input exceeds max_problem_bytes: {target}")
        value = json.loads(target.read_text(encoding=DEFAULT_ENCODING))
    except LayoutInputReadError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise LayoutInputReadError(f"cannot read JSON mapping from {target}: {exc}") from exc
    if not isinstance(value, dict):
        raise LayoutInputReadError(f"JSON root must be an object: {target}")
    return value


def _positive_finite(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise LayoutConfigError(f"{field_name} must be a positive finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise LayoutConfigError(f"{field_name} must be a positive finite number") from exc
    if not math.isfinite(number) or number <= 0.0:
        raise LayoutConfigError(f"{field_name} must be a positive finite number")
    return number


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise LayoutConfigError(f"{field_name} must be a positive integer")
    return value


def _required_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise LayoutConfigError(f"{field_name} must be an object")
    return value


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
        raise LayoutConfigError(f"{field_name} must be a non-empty string array")
    if len(set(value)) != len(value):
        raise LayoutConfigError(f"{field_name} must not contain duplicates")
    return tuple(value)


def _unique_ids(items: list[Any], id_field: str, context: str) -> set[str]:
    values: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise LayoutInputReferenceError(f"{context}[{index}] must be an object")
        object_id = item.get(id_field)
        if not isinstance(object_id, str) or not object_id:
            raise LayoutInputReferenceError(f"{context}[{index}].{id_field} must be non-empty")
        if object_id in values:
            raise LayoutInputReferenceError(f"duplicate {id_field}: {object_id}")
        values.add(object_id)
    return values


def _format_schema_error(error: jsonschema.ValidationError) -> str:
    location = ".".join(str(part) for part in error.absolute_path) or "$"
    return f"{location}: {error.message}"


# ============================================================
# 默认映射
# ============================================================

EXPECTED_FRAME = {
    "frame_id": INTERNAL_FRAME_ID,
    "handedness": "RIGHT",
    "z_axis": "UP",
    "length_unit": INTERNAL_LENGTH_UNIT,
}


# ============================================================
# 核心类
# ============================================================

class LayoutProblemLoader:
    """Load a standardized Phase 1 problem without interpreting domain semantics."""

    def __init__(self, config: Phase1Config, schema_path: str | Path) -> None:
        self.config = config
        self.schema_path = Path(schema_path)
        self.schema = _read_json_mapping(self.schema_path)
        try:
            jsonschema.Draft202012Validator.check_schema(self.schema)
        except jsonschema.SchemaError as exc:
            raise LayoutInputSchemaError(f"invalid layout problem schema: {exc.message}") from exc
        self.validator = jsonschema.Draft202012Validator(self.schema)

    def load(self, problem_path: str | Path) -> LayoutProblem:
        payload = _read_json_mapping(problem_path, max_bytes=self.config.max_problem_bytes)
        errors = sorted(self.validator.iter_errors(payload), key=lambda item: list(item.absolute_path))
        if errors:
            raise LayoutInputSchemaError(_format_schema_error(errors[0]))
        position_modes = self._validate_contract_semantics(payload)
        return LayoutProblem(
            schema_version=str(payload["schema_version"]),
            problem_id=str(payload["problem_id"]),
            payload=payload,
            position_modes=position_modes,
        )

    def _validate_contract_semantics(self, payload: Mapping[str, Any]) -> dict[str, str]:
        if payload["schema_version"] != self.config.problem_schema_version:
            raise LayoutInputSchemaError(
                f"problem schema_version expected {self.config.problem_schema_version}, "
                f"got {payload['schema_version']}"
            )
        if dict(payload["coordinate_frame"]) != EXPECTED_FRAME:
            raise LayoutInputSchemaError(
                "coordinate_frame must already be normalized to SC_TSE_CARTESIAN_RH, RIGHT, UP, m"
            )

        blocks = list(payload["blocks"])
        block_ids = _unique_ids(blocks, "block_id", "blocks")
        block_by_id = {str(item["block_id"]): item for item in blocks}
        constraint_ids = _unique_ids(list(payload["constraints"]), "constraint_id", "constraints")
        if len(constraint_ids) != len(payload["constraints"]):
            raise LayoutInputReferenceError("constraint identities are not stable")
        for constraint in payload["constraints"]:
            unknown = sorted(set(constraint["subjects"]) - block_ids - {"site_boundary"})
            if unknown:
                raise LayoutInputReferenceError(
                    f"constraint {constraint['constraint_id']} references unknown subjects: {unknown}"
                )

        controls = list(payload["variable_controls"])
        controlled_ids = _unique_ids(controls, "object_id", "variable_controls")
        missing = sorted(block_ids - controlled_ids)
        extra = sorted(controlled_ids - block_ids)
        if missing or extra:
            raise LayoutInputReferenceError(
                f"position controls must match blocks exactly; missing={missing}, extra={extra}"
            )
        position_modes: dict[str, str] = {}
        for control in controls:
            object_id = str(control["object_id"])
            mode = str(control["mode"])
            if mode not in self.config.supported_position_modes:
                raise LayoutInputSchemaError(f"unsupported position mode: {mode}")
            if mode == "FREE" and block_by_id[object_id]["geometry"]["type"] != "rectangle":
                raise LayoutInputSchemaError(
                    f"FREE position currently requires rectangle geometry: {object_id}"
                )
            position_modes[object_id] = mode

        solver_config = payload["solver_config"]
        strategy = str(solver_config["strategy"])
        if strategy not in self.config.allowed_strategies:
            raise LayoutInputSchemaError(f"strategy is not allowed by Phase 1 config: {strategy}")
        requested_budget = int(solver_config["max_candidate_evaluations"])
        if requested_budget > self.config.max_candidate_evaluations_limit:
            raise LayoutInputSchemaError(
                "max_candidate_evaluations exceeds configured capability limit"
            )
        return position_modes


# ============================================================
# Schema / 契约辅助函数
# ============================================================

def load_phase1_config(path: str | Path) -> Phase1Config:
    raw = _read_json_mapping(path)
    allowed = {
        "config_version", "contract_versions", "internal_coordinate_frame",
        "numerical_tolerance", "solver_capability", "runtime",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise LayoutConfigError(f"unknown config fields: {unknown}")
    if raw.get("config_version") != CONFIG_VERSION:
        raise LayoutConfigError(f"config_version must be {CONFIG_VERSION}")

    versions = _required_mapping(raw.get("contract_versions"), "contract_versions")
    frame = _required_mapping(raw.get("internal_coordinate_frame"), "internal_coordinate_frame")
    tolerance = _required_mapping(raw.get("numerical_tolerance"), "numerical_tolerance")
    capability = _required_mapping(raw.get("solver_capability"), "solver_capability")
    runtime = _required_mapping(raw.get("runtime"), "runtime")
    if dict(frame) != EXPECTED_FRAME:
        raise LayoutConfigError("internal_coordinate_frame must match the frozen SC-TSE frame")
    if versions.get("layout_problem") != PROBLEM_SCHEMA_VERSION:
        raise LayoutConfigError("layout_problem contract version does not match this script")
    if versions.get("layout_result") != RESULT_SCHEMA_VERSION:
        raise LayoutConfigError("layout_result contract version does not match this script")

    return Phase1Config(
        config_version=CONFIG_VERSION,
        problem_schema_version=PROBLEM_SCHEMA_VERSION,
        result_schema_version=RESULT_SCHEMA_VERSION,
        frame_id=INTERNAL_FRAME_ID,
        handedness="RIGHT",
        z_axis="UP",
        length_unit=INTERNAL_LENGTH_UNIT,
        tolerance=NumericalTolerance(
            linear_m=_positive_finite(tolerance.get("linear_m"), "numerical_tolerance.linear_m"),
            area_m2=_positive_finite(tolerance.get("area_m2"), "numerical_tolerance.area_m2"),
            z_m=_positive_finite(tolerance.get("z_m"), "numerical_tolerance.z_m"),
        ),
        allowed_strategies=_string_tuple(capability.get("allowed_strategies"), "solver_capability.allowed_strategies"),
        supported_constraint_types=_string_tuple(
            capability.get("supported_constraint_types"),
            "solver_capability.supported_constraint_types",
        ),
        supported_position_modes=_string_tuple(
            capability.get("supported_position_modes"),
            "solver_capability.supported_position_modes",
        ),
        max_candidate_evaluations_limit=_positive_int(
            capability.get("max_candidate_evaluations_limit"),
            "solver_capability.max_candidate_evaluations_limit",
        ),
        max_problem_bytes=_positive_int(runtime.get("max_problem_bytes"), "runtime.max_problem_bytes"),
    )


# ============================================================
# CLI / main 接口区
# ============================================================

# This contract gateway is import-only; the public CLI is run_sc_tse_phase1_v0001.py.
