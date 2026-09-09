# ============================================================
# 文件名: sc_tse_road_vertical_v0001.py
# 中文名: 道路设计标高与纵横坡计算
# 版本号: v0001
#
# 主层级: action
# 层级: development / sc_tse / phase4
# 脚本定位: road_vertical_provider
#
# 职责说明:
# - 道路设计标高与纵横坡计算
#
# 本脚本做什么:
# - RoadContext and alignment → VerticalResult with station elevations and analytic slope bounds
#
# 本脚本不做什么:
# - 不解释消防法规，不执行车辆扫掠或项目级自动重布局
#
# 制度边界声明:
# - 原始输入只读；无隐式数据库写入或联网调用
# - 持久化只在显式输出或测试临时目录，拒绝覆盖已有结果
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: sc_tse_road_vertical_v0001
# family: sc_tse_road_vertical
# role: road_vertical_provider
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/sc_tse/sc_tse_road_vertical_v0001.py
# input:
#   - RoadContext and alignment
# output:
#   - VerticalResult with station elevations and analytic slope bounds
# depends_on:
#   - sc_tse_road_contracts_v0001
#   - sc_tse_road_alignment_v0001
# used_by:
#   - run_sc_tse_phase4_v0001
# ============================================================

from __future__ import annotations

import math

from sc_tse_road_alignment_v0001 import angle_on_arc, point_at

DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "sc_tse_road_vertical"
SCRIPT_NAME = "sc_tse_road_vertical_v0001"
SCRIPT_VERSION = "v0001"


# ============================================================
# 数据结构
# ============================================================

# VerticalResult v0001 explicitly separates design elevations from terrain and height.


# ============================================================
# 工具函数区
# ============================================================

def primitive_max_grade(p: dict, gx: float, gy: float) -> float:
    if p["kind"] == "LINE":
        _, tangent = point_at(p, 0)
        return abs(gx * tangent[0] + gy * tangent[1])
    angles = [p["start_angle_rad"], p["start_angle_rad"] + p["sweep_rad"]]
    # |dot(gradient,tangent)| stationary points on the actual circular arc.
    angle = math.atan2(-gx, gy)
    angles += [a for a in (angle, angle + math.pi) if angle_on_arc(a, p)]
    return max(abs(-gx * math.sin(a) + gy * math.cos(a)) for a in angles)


# ============================================================
# 核心类
# ============================================================

def evaluate_vertical(context, alignment: dict) -> dict:
    model, c = context.problem["design_elevation"], context.config["vertical"]
    if model is None:
        return {"status": "NOT_EVALUATED", "reason": "DESIGN_ELEVATION_MISSING",
                "max_centerline_grade_ratio": None, "max_road_edge_grade_ratio": None,
                "cross_slope_ratio": None, "stations": [], "constraints": []}
    gx, gy = model["gradient_x"], model["gradient_y"]
    half, cross_slope = alignment["width_m"] / 2, c["cross_slope_ratio"]
    center_grades = [primitive_max_grade(p, gx, gy) for p in alignment["primitives"]]
    # Inner offset curves have shorter horizontal station length than their centerline.
    edge_grades = [g / (1 - half / p["radius_m"]) if p["kind"] == "ARC" else g
                   for p, g in zip(alignment["primitives"], center_grades)]
    tol = context.config["numerics"]["linear_tolerance_m"]
    maximum, maximum_edge = max(center_grades), max(edge_grades)
    origin = model["origin_xy"]
    stations = []
    for sample in alignment["samples"]:
        x, y = sample["xy"]
        tx, ty = sample["tangent"]
        z = model["origin_z_m"] + gx * (x - origin[0]) + gy * (y - origin[1])
        stations.append({"station_m": sample["station_m"], "center_xyz_m": [x, y, z],
                         "left_xyz_m": [x - ty * half, y + tx * half, z - half * cross_slope],
                         "right_xyz_m": [x + ty * half, y - tx * half, z - half * cross_slope]})
    constraints = [
        {"constraint_id": "longitudinal_slope", "status": "SATISFIED" if maximum_edge <= c["max_longitudinal_slope_ratio"] + tol else "VIOLATED",
         "measured": maximum_edge, "required_max": c["max_longitudinal_slope_ratio"],
         "method": "ANALYTIC_PLANE_GRADIENT_ON_LINE_AND_ARC_INCLUDING_OFFSET_EDGES"},
        {"constraint_id": "cross_slope", "status": "SATISFIED" if c["min_cross_slope_ratio"] - tol <= cross_slope <= c["max_cross_slope_ratio"] + tol else "VIOLATED",
         "measured": cross_slope, "required_min": c["min_cross_slope_ratio"], "required_max": c["max_cross_slope_ratio"],
         "method": "SYMMETRIC_CROWN_CONSTANT_CROSS_SLOPE"},
    ]
    return {"status": "SATISFIED" if all(x["status"] == "SATISFIED" for x in constraints) else "VIOLATED",
            "design_elevation": model, "max_centerline_grade_ratio": maximum,
            "max_road_edge_grade_ratio": maximum_edge, "cross_slope_ratio": cross_slope,
            "stations": stations, "constraints": constraints,
            "not_evaluated": ["vehicle_dynamics", "superelevation_transition", "vertical_curve_design",
                              "sight_distance", "hydraulic_drainage", "earthwork"]}

