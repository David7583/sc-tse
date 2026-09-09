# ============================================================
# 文件名: sc_tse_geometry_v0001.py
# 中文名: SC-TSE Phase 0 几何校验脚本
# 版本号: v0001
#
# 主层级: action
# 层级: development / sc_tse / geometry
# 脚本定位: 标准计算问题中的二维与基础 2.5D 几何事实计算边界
#
# 职责说明:
# - 校验 Rectangle、Polygon、Site 和 Z Interval 的计算合法性
# - 计算不泄漏 Shapely 类型的二维关系与基础 2.5D 碰撞结果
#
# 本脚本做什么:
# - 严格区分 DISJOINT、TOUCH、OVERLAP、CONTAIN 和 INVALID
# - 使用配置化 Numerical Tolerance 判断实质重叠和 Z 关系
#
# 本脚本不做什么:
# - 不自动修复或覆盖输入几何，不判断领域规则是否合法
# - 不执行布局、路径、道路工程、反馈编排或完整三维计算
#
# 制度边界声明:
# - Validation 与 Repair 分离；非法几何准确失败并保留原始输入
# - Numerical Tolerance 只处理数值误差，不放宽工程或规范约束
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: sc_tse_geometry_v0001
# family: sc_tse_geometry
# role: phase0_geometry_validation_and_relation
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/sc_tse/sc_tse_geometry_v0001.py
# input:
#   - validated ComputationalProblem payload
#   - Phase0Config numerical tolerance
# output:
#   - GeometryValidationResult
#   - GeometryRelationResult
# depends_on:
#   - Python stdlib: dataclasses, math, typing
#   - Shapely 2.x
#   - sc_tse_contracts_v0001
# used_by:
#   - run_sc_tse_phase0_v0001
#   - test_sc_tse_phase0_v0001
# ============================================================

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from shapely import affinity
from shapely.geometry import Polygon, box
from shapely.validation import explain_validity

from sc_tse_contracts_v0001 import NumericalTolerance


DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "sc_tse_geometry"
SCRIPT_NAME = "sc_tse_geometry_v0001"
SCRIPT_VERSION = "v0001"


# ============================================================
# 异常类型
# ============================================================

class ScTseGeometryError(RuntimeError):
    """Base error for SC-TSE Phase 0 geometry failures."""


class GeometryInputError(ScTseGeometryError):
    """Raised when geometry input is invalid and must not be repaired silently."""


# ============================================================
# 数据结构
# ============================================================

@dataclass(frozen=True)
class GeometryValidationResult:
    object_id: str
    valid: bool
    geometry_type: str
    area_m2: float
    validation_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GeometryRelationResult:
    subject_a: str
    subject_b: str
    relation_2d: str
    collision_2d: bool
    intersection_area_m2: float
    distance_m: float
    z_relation: str | None
    collision_2_5d: bool | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ============================================================
# 工具函数区
# ============================================================

def _finite_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise GeometryInputError(f"{field_name} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise GeometryInputError(f"{field_name} must be a finite number") from exc
    if not math.isfinite(number):
        raise GeometryInputError(f"{field_name} must be a finite number")
    return number


def _point(value: Sequence[Any], field_name: str) -> tuple[float, float]:
    if isinstance(value, (str, bytes)) or len(value) != 2:
        raise GeometryInputError(f"{field_name} must contain exactly two coordinates")
    return _finite_number(value[0], f"{field_name}[0]"), _finite_number(value[1], f"{field_name}[1]")


def _polygon_from_coordinates(
    coordinates: Sequence[Sequence[Any]],
    object_id: str,
    tolerance: NumericalTolerance,
) -> Polygon:
    if len(coordinates) < 4:
        raise GeometryInputError(f"{object_id}: polygon requires at least four coordinates including closure")
    points = tuple(_point(value, f"{object_id}.coordinates") for value in coordinates)
    if points[0] != points[-1]:
        raise GeometryInputError(f"{object_id}: polygon ring must be explicitly closed")
    if len(set(points[:-1])) < 3:
        raise GeometryInputError(f"{object_id}: polygon requires at least three distinct vertices")
    polygon = Polygon(points)
    if polygon.is_empty:
        raise GeometryInputError(f"{object_id}: polygon is empty")
    if not polygon.is_valid:
        raise GeometryInputError(f"{object_id}: invalid polygon: {explain_validity(polygon)}")
    if float(polygon.area) <= tolerance.area_m2:
        raise GeometryInputError(f"{object_id}: polygon area does not exceed numerical area tolerance")
    return polygon


def _rectangle_from_parameters(
    geometry: Mapping[str, Any],
    object_id: str,
    tolerance: NumericalTolerance,
) -> Polygon:
    center = _point(geometry["center"], f"{object_id}.center")
    width = _finite_number(geometry["width_m"], f"{object_id}.width_m")
    height = _finite_number(geometry["height_m"], f"{object_id}.height_m")
    angle = _finite_number(geometry.get("rotation_deg", 0.0), f"{object_id}.rotation_deg")
    if width <= tolerance.linear_m or height <= tolerance.linear_m:
        raise GeometryInputError(f"{object_id}: rectangle dimensions must exceed linear tolerance")
    polygon = box(
        center[0] - width / 2.0,
        center[1] - height / 2.0,
        center[0] + width / 2.0,
        center[1] + height / 2.0,
    )
    if abs(angle % 360.0) > 0.0:
        polygon = affinity.rotate(polygon, angle, origin=center, use_radians=False)
    return polygon


def _z_interval(block: Mapping[str, Any], object_id: str) -> tuple[float, float] | None:
    raw = block.get("z_interval")
    if raw is None:
        return None
    lower = _finite_number(raw["min_m"], f"{object_id}.z_interval.min_m")
    upper = _finite_number(raw["max_m"], f"{object_id}.z_interval.max_m")
    if upper < lower:
        raise GeometryInputError(f"{object_id}: z_interval.max_m must be >= min_m")
    return lower, upper


def _z_relation(
    a: tuple[float, float],
    b: tuple[float, float],
    tolerance: NumericalTolerance,
) -> str:
    overlap = min(a[1], b[1]) - max(a[0], b[0])
    if overlap > tolerance.z_m:
        return "OVERLAP"
    if overlap >= -tolerance.z_m:
        return "TOUCH"
    return "DISJOINT"


# ============================================================
# 默认映射
# ============================================================

SUPPORTED_GEOMETRY_TYPES = {"rectangle", "polygon"}


# ============================================================
# 核心类
# ============================================================

class GeometryEvaluator:
    """Evaluate geometry facts while keeping Shapely behind this strategy boundary."""

    def __init__(self, tolerance: NumericalTolerance) -> None:
        self.tolerance = tolerance

    def geometry(self, geometry: Mapping[str, Any], object_id: str) -> Polygon:
        geometry_type = geometry.get("type")
        if geometry_type == "polygon":
            return _polygon_from_coordinates(geometry["coordinates"], object_id, self.tolerance)
        if geometry_type == "rectangle":
            return _rectangle_from_parameters(geometry, object_id, self.tolerance)
        raise GeometryInputError(f"{object_id}: unsupported geometry type: {geometry_type}")

    def validate(self, geometry: Mapping[str, Any], object_id: str) -> GeometryValidationResult:
        polygon = self.geometry(geometry, object_id)
        return GeometryValidationResult(
            object_id=object_id,
            valid=True,
            geometry_type=str(geometry["type"]),
            area_m2=float(polygon.area),
            validation_reason=None,
        )

    def relation(
        self,
        block_a: Mapping[str, Any],
        block_b: Mapping[str, Any],
    ) -> GeometryRelationResult:
        id_a = str(block_a["block_id"])
        id_b = str(block_b["block_id"])
        shape_a = self.geometry(block_a["geometry"], id_a)
        shape_b = self.geometry(block_b["geometry"], id_b)
        intersection_area = float(shape_a.intersection(shape_b).area)
        collision_2d = intersection_area > self.tolerance.area_m2
        contains = collision_2d and (shape_a.covers(shape_b) or shape_b.covers(shape_a))
        numerical_touch = shape_a.distance(shape_b) <= self.tolerance.linear_m
        if contains:
            relation_2d = "CONTAIN"
        elif collision_2d:
            relation_2d = "OVERLAP"
        elif numerical_touch:
            relation_2d = "TOUCH"
        else:
            relation_2d = "DISJOINT"

        interval_a = _z_interval(block_a, id_a)
        interval_b = _z_interval(block_b, id_b)
        z_relation = None
        collision_2_5d = None
        if interval_a is not None and interval_b is not None:
            z_relation = _z_relation(interval_a, interval_b, self.tolerance)
            collision_2_5d = collision_2d and z_relation == "OVERLAP"

        return GeometryRelationResult(
            subject_a=id_a,
            subject_b=id_b,
            relation_2d=relation_2d,
            collision_2d=collision_2d,
            intersection_area_m2=intersection_area,
            distance_m=0.0 if numerical_touch else float(shape_a.distance(shape_b)),
            z_relation=z_relation,
            collision_2_5d=collision_2_5d,
        )


# ============================================================
# Schema / 契约辅助函数
# ============================================================

def validate_problem_geometries(
    payload: Mapping[str, Any],
    tolerance: NumericalTolerance,
) -> tuple[GeometryValidationResult, ...]:
    evaluator = GeometryEvaluator(tolerance)
    results = [evaluator.validate(payload["site"]["geometry"], str(payload["site"]["site_id"]))]
    for block in payload.get("blocks", []):
        _z_interval(block, str(block["block_id"]))
        results.append(evaluator.validate(block["geometry"], str(block["block_id"])))
    return tuple(results)


def evaluate_block_relations(
    payload: Mapping[str, Any],
    tolerance: NumericalTolerance,
) -> tuple[GeometryRelationResult, ...]:
    evaluator = GeometryEvaluator(tolerance)
    blocks = list(payload.get("blocks", []))
    results: list[GeometryRelationResult] = []
    for index, block_a in enumerate(blocks):
        for block_b in blocks[index + 1:]:
            results.append(evaluator.relation(block_a, block_b))
    return tuple(results)


# ============================================================
# CLI / main 接口区
# ============================================================

# This geometry strategy is import-only; the public CLI is run_sc_tse_phase0_v0001.py.
