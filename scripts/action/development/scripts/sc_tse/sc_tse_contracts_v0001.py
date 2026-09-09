# ============================================================
# 文件名: sc_tse_contracts_v0001.py
# 中文名: SC-TSE 计算问题契约脚本
# 版本号: v0001
#
# 主层级: action
# 层级: development / sc_tse / contracts
# 脚本定位: SC-TSE Phase 0 标准计算问题与配置的只读校验边界
#
# 职责说明:
# - 读取并校验版本化 Computational Problem JSON
# - 读取数值容差配置并校验跨对象引用和稳定身份
#
# 本脚本做什么:
# - 使用 JSON Schema 校验结构并执行 Schema 无法表达的引用一致性检查
# - 返回不携带第三方库类型的不可变配置与问题对象
#
# 本脚本不做什么:
# - 不解释领域语义或规范正文，不构造业务 Problem
# - 不执行几何计算、布局求解、工作流编排或持久化写入
#
# 制度边界声明:
# - 输入文件只读，未知字段和版本不匹配均准确失败
# - block_type 与 semantic_attributes 仅携带语义，不驱动 Core 领域分支
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: sc_tse_contracts_v0001
# family: sc_tse_contracts
# role: computational_problem_contract_gateway
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/sc_tse/sc_tse_contracts_v0001.py
# input:
#   - SC-TSE Phase 0 config JSON
#   - SC-TSE Computational Problem JSON
#   - sc_tse_computational_problem_v0001.schema.json
# output:
#   - validated Phase0Config and ComputationalProblem
# depends_on:
#   - Python stdlib: dataclasses, json, math, pathlib, typing
#   - jsonschema
# used_by:
#   - sc_tse_geometry_v0001
#   - run_sc_tse_phase0_v0001
#   - test_sc_tse_phase0_v0001
# ============================================================

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import jsonschema


DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "sc_tse_contracts"
SCRIPT_NAME = "sc_tse_contracts_v0001"
SCRIPT_VERSION = "v0001"

CONFIG_VERSION = "sc-tse-phase0-config-v0.1.0"
PROBLEM_SCHEMA_VERSION = "sc-tse-computational-problem-v0.1.0"
RESULT_SCHEMA_VERSION = "sc-tse-phase0-result-v0.1.0"
INTERNAL_FRAME_ID = "SC_TSE_CARTESIAN_RH"
INTERNAL_LENGTH_UNIT = "m"


# ============================================================
# 异常类型
# ============================================================

class ScTseContractError(RuntimeError):
    """Base error for SC-TSE Phase 0 contract failures."""


class ConfigError(ScTseContractError):
    """Raised when the versioned Phase 0 configuration is invalid."""


class InputReadError(ScTseContractError):
    """Raised when a JSON input cannot be read as a mapping."""


class InputSchemaError(ScTseContractError):
    """Raised when a Computational Problem violates its JSON Schema."""


class InputReferenceError(ScTseContractError):
    """Raised when stable identities or cross-object references are invalid."""


# ============================================================
# 数据结构
# ============================================================

@dataclass(frozen=True)
class NumericalTolerance:
    linear_m: float
    area_m2: float
    z_m: float


@dataclass(frozen=True)
class Phase0Config:
    config_version: str
    problem_schema_version: str
    result_schema_version: str
    frame_id: str
    handedness: str
    z_axis: str
    length_unit: str
    tolerance: NumericalTolerance
    max_problem_bytes: int


@dataclass(frozen=True)
class ComputationalProblem:
    schema_version: str
    problem_id: str
    payload: Mapping[str, Any]


# ============================================================
# 工具函数区
# ============================================================

def _read_json_mapping(path: str | Path, *, max_bytes: int | None = None) -> dict[str, Any]:
    target = Path(path)
    try:
        if max_bytes is not None and target.stat().st_size > max_bytes:
            raise InputReadError(f"JSON input exceeds max_problem_bytes: {target}")
        value = json.loads(target.read_text(encoding=DEFAULT_ENCODING))
    except InputReadError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise InputReadError(f"cannot read JSON mapping from {target}: {exc}") from exc
    if not isinstance(value, dict):
        raise InputReadError(f"JSON root must be an object: {target}")
    return value


def _positive_finite(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise ConfigError(f"{field_name} must be a positive finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field_name} must be a positive finite number") from exc
    if not math.isfinite(number) or number <= 0.0:
        raise ConfigError(f"{field_name} must be a positive finite number")
    return number


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{field_name} must be a positive integer")
    return value


def _required_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{field_name} must be an object")
    return value


def _unique_ids(items: list[Any], id_field: str, context: str) -> set[str]:
    values: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise InputReferenceError(f"{context}[{index}] must be an object")
        object_id = item.get(id_field)
        if not isinstance(object_id, str) or not object_id:
            raise InputReferenceError(f"{context}[{index}].{id_field} must be non-empty")
        if object_id in values:
            raise InputReferenceError(f"duplicate {id_field}: {object_id}")
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

class ComputationalProblemLoader:
    """Load a standardized problem without interpreting carried domain semantics."""

    def __init__(self, config: Phase0Config, schema_path: str | Path) -> None:
        self.config = config
        self.schema_path = Path(schema_path)
        self.schema = _read_json_mapping(self.schema_path)
        try:
            jsonschema.Draft202012Validator.check_schema(self.schema)
        except jsonschema.SchemaError as exc:
            raise InputSchemaError(f"invalid problem schema: {exc.message}") from exc
        self.validator = jsonschema.Draft202012Validator(self.schema)

    def load(self, problem_path: str | Path) -> ComputationalProblem:
        payload = _read_json_mapping(problem_path, max_bytes=self.config.max_problem_bytes)
        errors = sorted(self.validator.iter_errors(payload), key=lambda item: list(item.absolute_path))
        if errors:
            raise InputSchemaError(_format_schema_error(errors[0]))
        self._validate_contract_semantics(payload)
        return ComputationalProblem(
            schema_version=str(payload["schema_version"]),
            problem_id=str(payload["problem_id"]),
            payload=payload,
        )

    def _validate_contract_semantics(self, payload: Mapping[str, Any]) -> None:
        if payload["schema_version"] != self.config.problem_schema_version:
            raise InputSchemaError(
                f"problem schema_version expected {self.config.problem_schema_version}, "
                f"got {payload['schema_version']}"
            )
        frame = payload["coordinate_frame"]
        if dict(frame) != EXPECTED_FRAME:
            raise InputSchemaError(
                "coordinate_frame must already be normalized to SC_TSE_CARTESIAN_RH, RIGHT, UP, m"
            )

        blocks = list(payload.get("blocks", []))
        block_ids = _unique_ids(blocks, "block_id", "blocks")
        gate_ids: set[str] = set()
        gate_owners: dict[str, str] = {}
        for block in blocks:
            elevation = block.get("elevation")
            if isinstance(elevation, dict) and elevation.get("reference") == "relative" and not elevation.get("datum_id"):
                raise InputReferenceError(
                    f"relative elevation for block {block['block_id']} requires datum_id"
                )
            for gate in block.get("connection_gates", []):
                gate_id = gate["gate_id"]
                if gate_id in gate_ids:
                    raise InputReferenceError(f"duplicate gate_id: {gate_id}")
                gate_ids.add(gate_id)
                gate_owners[gate_id] = str(block["block_id"])

        _unique_ids(list(payload.get("constraints", [])), "constraint_id", "constraints")
        for constraint in payload.get("constraints", []):
            unknown = sorted(set(constraint["subjects"]) - block_ids - gate_ids - {"site_boundary"})
            if unknown:
                raise InputReferenceError(
                    f"constraint {constraint['constraint_id']} references unknown subjects: {unknown}"
                )

        _unique_ids(
            list(payload.get("connection_requirements", [])),
            "connection_id",
            "connection_requirements",
        )
        for connection in payload.get("connection_requirements", []):
            if connection["source_block"] not in block_ids:
                raise InputReferenceError(
                    f"connection {connection['connection_id']} references unknown source_block"
                )
            if connection["target_block"] not in block_ids:
                raise InputReferenceError(
                    f"connection {connection['connection_id']} references unknown target_block"
                )
            for field_name in ("source_gate", "target_gate"):
                gate_id = connection.get(field_name)
                if gate_id is not None and gate_id not in gate_ids:
                    raise InputReferenceError(
                        f"connection {connection['connection_id']} references unknown {field_name}: {gate_id}"
                    )
            source_gate = connection.get("source_gate")
            target_gate = connection.get("target_gate")
            if source_gate is not None and gate_owners[source_gate] != connection["source_block"]:
                raise InputReferenceError(
                    f"connection {connection['connection_id']} source_gate does not belong to source_block"
                )
            if target_gate is not None and gate_owners[target_gate] != connection["target_block"]:
                raise InputReferenceError(
                    f"connection {connection['connection_id']} target_gate does not belong to target_block"
                )


# ============================================================
# Schema / 契约辅助函数
# ============================================================

def load_phase0_config(path: str | Path) -> Phase0Config:
    raw = _read_json_mapping(path)
    allowed = {
        "config_version", "contract_versions", "internal_coordinate_frame",
        "numerical_tolerance", "runtime",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ConfigError(f"unknown config fields: {unknown}")
    if raw.get("config_version") != CONFIG_VERSION:
        raise ConfigError(f"config_version must be {CONFIG_VERSION}")

    versions = _required_mapping(raw.get("contract_versions"), "contract_versions")
    frame = _required_mapping(raw.get("internal_coordinate_frame"), "internal_coordinate_frame")
    tolerance = _required_mapping(raw.get("numerical_tolerance"), "numerical_tolerance")
    runtime = _required_mapping(raw.get("runtime"), "runtime")
    if dict(frame) != EXPECTED_FRAME:
        raise ConfigError("internal_coordinate_frame must match the frozen SC-TSE frame")
    problem_version = versions.get("problem")
    result_version = versions.get("result")
    if problem_version != PROBLEM_SCHEMA_VERSION or result_version != RESULT_SCHEMA_VERSION:
        raise ConfigError("contract_versions do not match this script version")

    return Phase0Config(
        config_version=CONFIG_VERSION,
        problem_schema_version=str(problem_version),
        result_schema_version=str(result_version),
        frame_id=INTERNAL_FRAME_ID,
        handedness="RIGHT",
        z_axis="UP",
        length_unit=INTERNAL_LENGTH_UNIT,
        tolerance=NumericalTolerance(
            linear_m=_positive_finite(tolerance.get("linear_m"), "numerical_tolerance.linear_m"),
            area_m2=_positive_finite(tolerance.get("area_m2"), "numerical_tolerance.area_m2"),
            z_m=_positive_finite(tolerance.get("z_m"), "numerical_tolerance.z_m"),
        ),
        max_problem_bytes=_positive_int(runtime.get("max_problem_bytes"), "runtime.max_problem_bytes"),
    )


# ============================================================
# CLI / main 接口区
# ============================================================

# This contract gateway is import-only; the public CLI is run_sc_tse_phase0_v0001.py.
