# ============================================================
# 文件名: sc_tse_constraint_evaluator_v0001.py
# 中文名: SC-TSE Phase 1 通用约束评估脚本
# 版本号: v0001
#
# 主层级: action
# 层级: development / sc_tse / phase1_constraint_evaluation
# 脚本定位: 已编译空间 Constraint 的通用几何事实评估策略
#
# 职责说明:
# - 评估 inside_site、non_overlap 和 min_distance 计算约束
# - 输出结构化约束状态并严格区分 HARD 与 SOFT
#
# 本脚本做什么:
# - 对完整或部分布局执行可判定约束检查
# - 使用 Numerical Tolerance 处理数值边界并返回原始观测证据
#
# 本脚本不做什么:
# - 不解析规范正文、block_type、semantic_attributes 或领域词义
# - 不移动体块、不选择下一轮动作、不执行连接或路径计算
#
# 制度边界声明:
# - 未支持的约束类型显式报告 SolverCapabilityError，禁止静默忽略
# - Shapely 类型只存在于本计算策略内部，不进入公开 Contract
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: sc_tse_constraint_evaluator_v0001
# family: sc_tse_constraint_evaluator
# role: phase1_compiled_constraint_evaluator
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/sc_tse/sc_tse_constraint_evaluator_v0001.py
# input:
#   - validated LayoutProblem payload
#   - primitive block mappings
# output:
#   - ConstraintStatus records with observed and required evidence
# depends_on:
#   - Python stdlib: dataclasses, math, pathlib, sys, typing
#   - Shapely 2.x through sc_tse_geometry_v0001 strategy
#   - sc_tse_contracts_v0001
#   - sc_tse_geometry_v0001
#   - sc_tse_layout_contracts_v0001
# used_by:
#   - sc_tse_basic_layout_solver_v0001
#   - test_sc_tse_phase1_v0001
# ============================================================

from __future__ import annotations

import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from sc_tse_layout_contracts_v0001 import Phase1Config


DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "sc_tse_constraint_evaluator"
SCRIPT_NAME = "sc_tse_constraint_evaluator_v0001"
SCRIPT_VERSION = "v0001"


# ============================================================
# Phase 0 几何策略导入
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
    if (CURRENT_MODULE_ROOT / "sc_tse_geometry_v0001.py").is_file()
    else FORMAL_MODULE_ROOT
)
for module_root in (PROJECT_ROOT, PHASE0_MODULE_ROOT):
    if str(module_root) not in sys.path:
        sys.path.insert(0, str(module_root))

from sc_tse_geometry_v0001 import (  # noqa: E402
    GeometryEvaluator,
)


# ============================================================
# 异常类型
# ============================================================

class ScTseConstraintEvaluationError(RuntimeError):
    """Base error for Phase 1 compiled constraint evaluation."""


class SolverCapabilityError(ScTseConstraintEvaluationError):
    """Raised when the selected strategy cannot evaluate a supplied constraint."""


class ConstraintParameterError(ScTseConstraintEvaluationError):
    """Raised when a compiled constraint has invalid computational parameters."""


# ============================================================
# 数据结构
# ============================================================

@dataclass(frozen=True)
class ConstraintStatus:
    constraint_id: str
    constraint_type: str
    hardness: str
    satisfied: bool
    subjects: tuple[str, ...]
    observed: Any
    required: Any

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["subjects"] = list(self.subjects)
        return value


# ============================================================
# 工具函数区
# ============================================================

def _non_negative_finite(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise ConstraintParameterError(f"{field_name} must be a non-negative finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ConstraintParameterError(f"{field_name} must be a non-negative finite number") from exc
    if not math.isfinite(number) or number < 0.0:
        raise ConstraintParameterError(f"{field_name} must be a non-negative finite number")
    return number


def _block_subjects(constraint: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(str(item) for item in constraint["subjects"] if item != "site_boundary")


def _require_distinct_subjects(
    constraint: Mapping[str, Any],
    count: int,
) -> tuple[str, ...]:
    subjects = _block_subjects(constraint)
    if len(subjects) != count or len(set(subjects)) != count:
        raise ConstraintParameterError(
            f"constraint {constraint['constraint_id']} requires {count} distinct block subjects"
        )
    return subjects


# ============================================================
# 核心类
# ============================================================

class ConstraintEvaluator:
    """Evaluate already compiled generic constraints without domain interpretation."""

    def __init__(self, config: Phase1Config) -> None:
        self.config = config
        self.geometry = GeometryEvaluator(config.tolerance)

    def evaluate_available(
        self,
        site: Mapping[str, Any],
        blocks: Mapping[str, Mapping[str, Any]],
        constraints: Sequence[Mapping[str, Any]],
    ) -> tuple[ConstraintStatus, ...]:
        results: list[ConstraintStatus] = []
        available_ids = set(blocks)
        for constraint in constraints:
            constraint_type = str(constraint["type"])
            if constraint_type not in self.config.supported_constraint_types:
                raise SolverCapabilityError(
                    f"unsupported constraint type for current strategy: {constraint_type}"
                )
            block_subjects = set(_block_subjects(constraint))
            if not block_subjects.issubset(available_ids):
                continue
            results.append(self._evaluate_one(site, blocks, constraint))
        return tuple(results)

    def _evaluate_one(
        self,
        site: Mapping[str, Any],
        blocks: Mapping[str, Mapping[str, Any]],
        constraint: Mapping[str, Any],
    ) -> ConstraintStatus:
        constraint_type = str(constraint["type"])
        if constraint_type == "inside_site":
            return self._inside_site(site, blocks, constraint)
        if constraint_type == "non_overlap":
            return self._non_overlap(blocks, constraint)
        if constraint_type == "min_distance":
            return self._min_distance(blocks, constraint)
        raise SolverCapabilityError(
            f"unsupported constraint type for current strategy: {constraint_type}"
        )

    def _inside_site(
        self,
        site: Mapping[str, Any],
        blocks: Mapping[str, Mapping[str, Any]],
        constraint: Mapping[str, Any],
    ) -> ConstraintStatus:
        subject = _require_distinct_subjects(constraint, 1)[0]
        site_shape = self.geometry.geometry(site["geometry"], str(site["site_id"]))
        block_shape = self.geometry.geometry(blocks[subject]["geometry"], subject)
        outside_area = float(block_shape.difference(site_shape).area)
        satisfied = site_shape.buffer(self.config.tolerance.linear_m).covers(block_shape)
        return ConstraintStatus(
            constraint_id=str(constraint["constraint_id"]),
            constraint_type="inside_site",
            hardness=str(constraint["hardness"]),
            satisfied=bool(satisfied),
            subjects=tuple(str(item) for item in constraint["subjects"]),
            observed={"outside_area_m2": outside_area},
            required={"inside_site": True},
        )

    def _non_overlap(
        self,
        blocks: Mapping[str, Mapping[str, Any]],
        constraint: Mapping[str, Any],
    ) -> ConstraintStatus:
        subject_a, subject_b = _require_distinct_subjects(constraint, 2)
        shape_a = self.geometry.geometry(blocks[subject_a]["geometry"], subject_a)
        shape_b = self.geometry.geometry(blocks[subject_b]["geometry"], subject_b)
        intersection_area = float(shape_a.intersection(shape_b).area)
        satisfied = intersection_area <= self.config.tolerance.area_m2
        return ConstraintStatus(
            constraint_id=str(constraint["constraint_id"]),
            constraint_type="non_overlap",
            hardness=str(constraint["hardness"]),
            satisfied=bool(satisfied),
            subjects=tuple(str(item) for item in constraint["subjects"]),
            observed={"intersection_area_m2": intersection_area},
            required={"maximum_intersection_area_m2": self.config.tolerance.area_m2},
        )

    def _min_distance(
        self,
        blocks: Mapping[str, Mapping[str, Any]],
        constraint: Mapping[str, Any],
    ) -> ConstraintStatus:
        subject_a, subject_b = _require_distinct_subjects(constraint, 2)
        required_distance = _non_negative_finite(
            constraint["parameters"].get("distance_m"),
            f"constraint {constraint['constraint_id']}.parameters.distance_m",
        )
        shape_a = self.geometry.geometry(blocks[subject_a]["geometry"], subject_a)
        shape_b = self.geometry.geometry(blocks[subject_b]["geometry"], subject_b)
        observed_distance = float(shape_a.distance(shape_b))
        satisfied = observed_distance + self.config.tolerance.linear_m >= required_distance
        return ConstraintStatus(
            constraint_id=str(constraint["constraint_id"]),
            constraint_type="min_distance",
            hardness=str(constraint["hardness"]),
            satisfied=bool(satisfied),
            subjects=tuple(str(item) for item in constraint["subjects"]),
            observed={"distance_m": observed_distance},
            required={"minimum_distance_m": required_distance},
        )


# ============================================================
# Schema / 契约辅助函数
# ============================================================

def hard_violations(statuses: Sequence[ConstraintStatus]) -> tuple[ConstraintStatus, ...]:
    return tuple(item for item in statuses if item.hardness == "HARD" and not item.satisfied)


def soft_constraint_score(statuses: Sequence[ConstraintStatus]) -> float:
    soft = tuple(item for item in statuses if item.hardness == "SOFT")
    if not soft:
        return 1.0
    return sum(1 for item in soft if item.satisfied) / len(soft)


# ============================================================
# CLI / main 接口区
# ============================================================

# This evaluator is import-only; the public CLI is run_sc_tse_phase1_v0001.py.
