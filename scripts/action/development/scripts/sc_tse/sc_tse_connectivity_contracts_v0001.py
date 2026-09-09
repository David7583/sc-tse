# ============================================================
# 文件名: sc_tse_connectivity_contracts_v0001.py
# 中文名: SC-TSE Phase 2 连通性问题契约脚本
# 版本号: v0001
#
# 主层级: action
# 层级: development / sc_tse / phase2_connectivity_contracts
# 脚本定位: ConnectivityProblem、Gate 引用、Buffer 和计算预算的只读契约边界
#
# 职责说明:
# - 严格校验标准化 ConnectivityProblem 与 Phase 2 配置
# - 校验 Block、Gate、ConnectionRequirement 和 Obstacle Buffer 稳定引用
#
# 本脚本做什么:
# - 执行 JSON Schema、版本、坐标、唯一身份和能力上限检查
# - 返回不携带第三方库类型的不可变配置和问题对象
#
# 本脚本不做什么:
# - 不解释 connection_type、gate_type 或 block_type 的领域含义
# - 不搜索 Gateway/Path，不修改 Layout，不执行反馈编排
#
# 制度边界声明:
# - 领域规则必须在进入 Core 前编译为 ConnectionRequirement 和数值参数
# - required connection 必须显式引用属于对应 Block 的 Gate
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: sc_tse_connectivity_contracts_v0001
# family: sc_tse_connectivity_contracts
# role: phase2_connectivity_problem_contract_gateway
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/sc_tse/sc_tse_connectivity_contracts_v0001.py
# input:
#   - config/action/config/sc_tse_phase2_config_v0001.json
#   - SC-TSE ConnectivityProblem JSON
# output:
#   - validated Phase2Config and ConnectivityProblem
# depends_on:
#   - Python stdlib: dataclasses, json, math, pathlib, sys, typing
#   - jsonschema
#   - sc_tse_contracts_v0001
# used_by:
#   - sc_tse_connectivity_engine_v0001
#   - run_sc_tse_phase2_v0001
#   - test_sc_tse_phase2_v0001
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
SCRIPT_FAMILY = "sc_tse_connectivity_contracts"
SCRIPT_NAME = "sc_tse_connectivity_contracts_v0001"
SCRIPT_VERSION = "v0001"
CONFIG_VERSION = "sc-tse-phase2-config-v0.1.0"
PROBLEM_SCHEMA_VERSION = "sc-tse-connectivity-problem-v0.1.0"
RESULT_SCHEMA_VERSION = "sc-tse-connectivity-result-v0.1.0"


def _find_project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "PROGRAMMING_LANGUAGE_SCRIPT_FORMAT.md").is_file():
            return parent
    raise RuntimeError("cannot locate project root from script path")


PROJECT_ROOT = _find_project_root()
FORMAL_MODULE_ROOT = PROJECT_ROOT / "scripts" / "action" / "development" / "scripts" / "sc_tse"
for module_root in (PROJECT_ROOT, FORMAL_MODULE_ROOT):
    if str(module_root) not in sys.path:
        sys.path.insert(0, str(module_root))

from sc_tse_contracts_v0001 import NumericalTolerance  # noqa: E402


# ============================================================
# 异常类型
# ============================================================

class ScTseConnectivityContractError(RuntimeError):
    """Base error for Phase 2 connectivity contract failures."""


class ConnectivityConfigError(ScTseConnectivityContractError):
    """Raised when the Phase 2 configuration is invalid."""


class ConnectivityInputError(ScTseConnectivityContractError):
    """Raised when the ConnectivityProblem is invalid."""


# ============================================================
# 数据结构
# ============================================================

@dataclass(frozen=True)
class Phase2Config:
    result_schema_version: str
    tolerance: NumericalTolerance
    allowed_path_strategies: tuple[str, ...]
    max_grid_expansions_limit: int
    max_grid_cells_limit: int
    max_problem_bytes: int


@dataclass(frozen=True)
class ConnectivityProblem:
    problem_id: str
    payload: Mapping[str, Any]
    blocks: Mapping[str, Mapping[str, Any]]
    gates: Mapping[str, Mapping[str, Any]]
    gate_owners: Mapping[str, str]
    obstacle_buffers: Mapping[str, float]


# ============================================================
# 工具函数区
# ============================================================

def _read_mapping(path: str | Path, max_bytes: int | None = None) -> dict[str, Any]:
    target = Path(path)
    try:
        if max_bytes is not None and target.stat().st_size > max_bytes:
            raise ConnectivityInputError("input exceeds max_problem_bytes")
        value = json.loads(target.read_text(encoding=DEFAULT_ENCODING))
    except ConnectivityInputError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise ConnectivityInputError(f"cannot read JSON object from {target}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConnectivityInputError("JSON root must be an object")
    return value


def _positive(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ConnectivityConfigError(f"{name} must be positive and finite")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ConnectivityConfigError(f"{name} must be positive and finite") from exc
    if not math.isfinite(number) or number <= 0:
        raise ConnectivityConfigError(f"{name} must be positive and finite")
    return number


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConnectivityConfigError(f"{name} must be a positive integer")
    return value


def _strings(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
        raise ConnectivityConfigError(f"{name} must be a non-empty string array")
    return tuple(value)


def _input_number(value: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ConnectivityInputError(f"{name} must be finite" + (" and positive" if positive else ""))
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ConnectivityInputError(f"{name} must be finite" + (" and positive" if positive else "")) from exc
    if not math.isfinite(number) or (positive and number <= 0):
        raise ConnectivityInputError(f"{name} must be finite" + (" and positive" if positive else ""))
    return number


# ============================================================
# 默认映射
# ============================================================

EXPECTED_FRAME = {"frame_id": "SC_TSE_CARTESIAN_RH", "handedness": "RIGHT", "z_axis": "UP", "length_unit": "m"}


# ============================================================
# 核心类
# ============================================================

class ConnectivityProblemLoader:
    def __init__(self, config: Phase2Config, schema_path: str | Path) -> None:
        self.config = config
        schema = _read_mapping(schema_path)
        jsonschema.Draft202012Validator.check_schema(schema)
        self.validator = jsonschema.Draft202012Validator(schema)

    def load(self, path: str | Path) -> ConnectivityProblem:
        payload = _read_mapping(path, self.config.max_problem_bytes)
        errors = sorted(self.validator.iter_errors(payload), key=lambda item: list(item.absolute_path))
        if errors:
            location = ".".join(str(part) for part in errors[0].absolute_path) or "$"
            raise ConnectivityInputError(f"{location}: {errors[0].message}")
        if dict(payload["coordinate_frame"]) != EXPECTED_FRAME:
            raise ConnectivityInputError("coordinate_frame must already be normalized")

        blocks: dict[str, Mapping[str, Any]] = {}
        gates: dict[str, Mapping[str, Any]] = {}
        owners: dict[str, str] = {}
        for block in payload["blocks"]:
            block_id = str(block["block_id"])
            if block_id in blocks:
                raise ConnectivityInputError(f"duplicate block_id: {block_id}")
            blocks[block_id] = block
            for gate in block["connection_gates"]:
                gate_id = str(gate["gate_id"])
                if gate_id in gates:
                    raise ConnectivityInputError(f"duplicate gate_id: {gate_id}")
                gates[gate_id] = gate
                owners[gate_id] = block_id
                _input_number(gate["position"][0], f"{gate_id}.position[0]")
                _input_number(gate["position"][1], f"{gate_id}.position[1]")

        connection_ids: set[str] = set()
        for connection in payload["connection_requirements"]:
            connection_id = str(connection["connection_id"])
            if connection_id in connection_ids:
                raise ConnectivityInputError(f"duplicate connection_id: {connection_id}")
            connection_ids.add(connection_id)
            source_gate = str(connection["source_gate"])
            target_gate = str(connection["target_gate"])
            if source_gate not in gates or target_gate not in gates:
                raise ConnectivityInputError(f"connection {connection_id} references unknown gate")
            if owners[source_gate] != connection["source_block"] or owners[target_gate] != connection["target_block"]:
                raise ConnectivityInputError(f"connection {connection_id} gate ownership mismatch")
            connection_type = str(connection["connection_type"])
            if connection_type not in gates[source_gate]["allowed_connections"] or connection_type not in gates[target_gate]["allowed_connections"]:
                raise ConnectivityInputError(f"connection {connection_id} is not allowed by both referenced gates")

        buffers: dict[str, float] = {}
        for item in payload["obstacle_buffers"]:
            block_id = str(item["block_id"])
            if block_id in buffers:
                raise ConnectivityInputError(f"duplicate obstacle buffer: {block_id}")
            buffers[block_id] = _input_number(item["buffer_m"], f"{block_id}.buffer_m")
            if buffers[block_id] < 0:
                raise ConnectivityInputError(f"{block_id}.buffer_m must be non-negative")
        if set(buffers) != set(blocks):
            raise ConnectivityInputError("obstacle_buffers must match blocks exactly")

        solver = payload["solver_config"]
        for field in ("initial_probe_distance_m", "gateway_search_step_m", "max_gateway_distance_m", "grid_resolution_m"):
            _input_number(solver[field], field, positive=True)
        if solver["path_strategy"] not in self.config.allowed_path_strategies:
            raise ConnectivityInputError("path strategy is not allowed")
        if solver["max_grid_expansions"] > self.config.max_grid_expansions_limit:
            raise ConnectivityInputError("max_grid_expansions exceeds configured limit")
        if solver["initial_probe_distance_m"] > solver["max_gateway_distance_m"]:
            raise ConnectivityInputError("initial probe distance exceeds max gateway distance")
        return ConnectivityProblem(str(payload["problem_id"]), payload, blocks, gates, owners, buffers)


# ============================================================
# Schema / 契约辅助函数
# ============================================================

def load_phase2_config(path: str | Path) -> Phase2Config:
    raw = _read_mapping(path)
    if set(raw) != {"config_version", "contract_versions", "internal_coordinate_frame", "numerical_tolerance", "solver_capability", "runtime"}:
        raise ConnectivityConfigError("Phase 2 config fields do not match the versioned contract")
    if raw["config_version"] != CONFIG_VERSION or dict(raw["internal_coordinate_frame"]) != EXPECTED_FRAME:
        raise ConnectivityConfigError("Phase 2 config version or coordinate frame mismatch")
    versions = raw["contract_versions"]
    tolerance = raw["numerical_tolerance"]
    capability = raw["solver_capability"]
    runtime = raw["runtime"]
    expected_nested = (
        (versions, {"connectivity_problem", "connectivity_result"}, "contract_versions"),
        (tolerance, {"linear_m", "area_m2", "z_m"}, "numerical_tolerance"),
        (capability, {"allowed_path_strategies", "max_grid_expansions_limit", "max_grid_cells_limit"}, "solver_capability"),
        (runtime, {"max_problem_bytes"}, "runtime"),
    )
    for value, fields, name in expected_nested:
        if not isinstance(value, dict) or set(value) != fields:
            raise ConnectivityConfigError(f"{name} fields do not match the versioned contract")
    if versions.get("connectivity_problem") != PROBLEM_SCHEMA_VERSION or versions.get("connectivity_result") != RESULT_SCHEMA_VERSION:
        raise ConnectivityConfigError("contract version mismatch")
    return Phase2Config(
        result_schema_version=RESULT_SCHEMA_VERSION,
        tolerance=NumericalTolerance(_positive(tolerance["linear_m"], "linear_m"), _positive(tolerance["area_m2"], "area_m2"), _positive(tolerance["z_m"], "z_m")),
        allowed_path_strategies=_strings(capability["allowed_path_strategies"], "allowed_path_strategies"),
        max_grid_expansions_limit=_positive_int(capability["max_grid_expansions_limit"], "max_grid_expansions_limit"),
        max_grid_cells_limit=_positive_int(capability["max_grid_cells_limit"], "max_grid_cells_limit"),
        max_problem_bytes=_positive_int(runtime["max_problem_bytes"], "max_problem_bytes"),
    )


# ============================================================
# CLI / main 接口区
# ============================================================

# Import-only contract gateway; public CLI is run_sc_tse_phase2_v0001.py.
