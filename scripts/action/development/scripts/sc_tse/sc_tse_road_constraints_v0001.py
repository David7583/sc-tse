# ============================================================
# 文件名: sc_tse_road_constraints_v0001.py
# 中文名: 道路平面约束与闭环证据校核
# 版本号: v0001
#
# 主层级: action
# 层级: development / sc_tse / phase4
# 脚本定位: road_constraint_provider
#
# 职责说明:
# - 道路平面约束与闭环证据校核
#
# 本脚本做什么:
# - 输出退界、闭环、建筑包围与连接点的分项计算证据
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
# alias: sc_tse_road_constraints_v0001
# family: sc_tse_road_constraints
# role: road_constraint_provider
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/sc_tse/sc_tse_road_constraints_v0001.py
# input:
#   - RoadContext and constructed alignment
# output:
#   - RoadConstraintResult with per-object evidence
# depends_on:
#   - sc_tse_road_contracts_v0001
#   - sc_tse_road_alignment_v0001
#   - Shapely
# used_by:
#   - run_sc_tse_phase4_v0001
# ============================================================

from __future__ import annotations

import math

from shapely.geometry import LineString, Point, Polygon

from sc_tse_road_alignment_v0001 import (
    alignment_distance, alignment_point_distance, angle_on_arc, edge_setbacks,
    norm, point_at, primitive_segment_distance, sub,
)

DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "sc_tse_road_constraints"
SCRIPT_NAME = "sc_tse_road_constraints_v0001"
SCRIPT_VERSION = "v0001"


# ============================================================
# 数据结构
# ============================================================

# RoadConstraintResult v0001: status, constraints[{constraint_id,status,
# measured,required,margin,evidence}], connected_component_count, cycle_count.


# ============================================================
# 工具函数区
# ============================================================

def primitives_intersect(a: dict, b: dict, tolerance: float) -> bool:
    if b["kind"] == "LINE":
        return primitive_segment_distance(a, b["start"], b["end"]) <= tolerance
    if a["kind"] == "LINE":
        return primitive_segment_distance(b, a["start"], a["end"]) <= tolerance
    c1, c2, r1, r2 = a["center"], b["center"], a["radius_m"], b["radius_m"]
    delta, distance = sub(c2, c1), norm(sub(c2, c1))
    if distance <= tolerance:
        if abs(r1 - r2) > tolerance:
            return False
        return any(angle_on_arc(math.atan2(q[1] - c1[1], q[0] - c1[0]), a)
                   for q in (b["start"], b["end"])) or any(
                   angle_on_arc(math.atan2(q[1] - c2[1], q[0] - c2[0]), b) for q in (a["start"], a["end"]))
    if distance > r1 + r2 + tolerance or distance < abs(r1 - r2) - tolerance:
        return False
    along = (r1 * r1 - r2 * r2 + distance * distance) / (2 * distance)
    height = math.sqrt(max(0, r1 * r1 - along * along))
    base = (c1[0] + along * delta[0] / distance, c1[1] + along * delta[1] / distance)
    for sign in (-1, 1):
        q = (base[0] - sign * height * delta[1] / distance, base[1] + sign * height * delta[0] / distance)
        if all(angle_on_arc(math.atan2(q[1] - arc["center"][1], q[0] - arc["center"][0]), arc) for arc in (a, b)):
            return True
    return False


def check_record(name: str, measured, required, satisfied: bool, evidence: dict, margin=None) -> dict:
    return {"constraint_id": name, "status": "SATISFIED" if satisfied else "VIOLATED",
            "measured": measured, "required": required, "margin": margin, "evidence": evidence}


# ============================================================
# 核心类
# ============================================================

def evaluate_road(context, alignment: dict) -> dict:
    p, c = context.problem, context.config
    tol = c["numerics"]["linear_tolerance_m"]
    primitives, half = alignment["primitives"], alignment["width_m"] / 2
    samples = alignment["samples"]
    xy = [s["xy"] for s in samples]
    # Canonical endpoint equality avoids floating trigonometry introducing a fake opening.
    closed = norm(sub(primitives[0]["start"], primitives[-1]["end"])) <= tol
    if closed:
        xy[-1] = list(xy[0])
    sampled_line = LineString(xy)
    site = Polygon(p["site"]["boundary"])
    checks = []
    adjacency = all(norm(sub(a["end"], b["start"])) <= tol for a, b in zip(primitives, primitives[1:]))
    intersections = []
    for i, a in enumerate(primitives):
        for j in range(i + 2, len(primitives)):
            if closed and i == 0 and j == len(primitives) - 1:
                continue
            if primitives_intersect(a, primitives[j], tol):
                intersections.append([i, j])
    simple = sampled_line.is_simple and not intersections
    checks.append(check_record("route_continuity", adjacency, True, adjacency, {}))
    checks.append(check_record("simple_centerline", simple, True, simple, {"intersecting_primitives": intersections}))
    checks.append(check_record("closed_loop", closed, p["requirements"]["closed_loop"],
                               closed or not p["requirements"]["closed_loop"], {}))
    for index, (a, b, setback) in enumerate(zip(p["site"]["boundary"], p["site"]["boundary"][1:], edge_setbacks(p, c))):
        distance = alignment_distance(primitives, [a, b])
        margin = distance - half - setback
        checks.append(check_record(f"road_redline:{index}", distance - half, setback, margin >= -tol,
                                   {"edge_index": index, "distance_method": "ANALYTIC_LINE_ARC_SEGMENT"}, margin))
    inside = all(site.buffer(tol).covers(Point(point_at(segment, 0.5)[0])) for segment in primitives)
    checks.append(check_record("road_inside_site", inside, True, inside, {}))
    radii = [x["radius_m"] for x in primitives if x["kind"] == "ARC"]
    minimum_radius = min(radii) if radii else None
    required_radius = c["turning"]["centerline_radius_m"]
    checks.append(check_record("centerline_turn_radius", minimum_radius, required_radius,
                               minimum_radius is None or minimum_radius + tol >= required_radius,
                               {"reference": "CENTERLINE; does not prove vehicle swept path"}))
    enclosed_shape = Polygon(xy) if closed and simple else None
    for building in p["buildings"]:
        footprint = Polygon(building["footprint"])
        identifier = building["block_id"]
        redline_distance = site.boundary.distance(footprint)
        margin = redline_distance - c["setbacks"]["building_redline_m"]
        checks.append(check_record(f"building_redline:{identifier}", redline_distance, c["setbacks"]["building_redline_m"],
                                   site.buffer(tol).covers(footprint) and margin >= -tol, {"block_id": identifier}, margin))
        center_distance = alignment_distance(primitives, building["footprint"])
        penetrates = any(footprint.contains(Point(point_at(segment, 0.5)[0])) for segment in primitives)
        margin = center_distance - half - c["setbacks"]["building_road_m"]
        checks.append(check_record(f"building_road:{identifier}", center_distance - half, c["setbacks"]["building_road_m"],
                                   not penetrates and margin >= -tol,
                                   {"block_id": identifier, "distance_method": "ANALYTIC_LINE_ARC_SEGMENT"}, margin))
        if identifier in p["requirements"]["enclosed_block_ids"]:
            contained = enclosed_shape is not None and enclosed_shape.covers(footprint)
            checks.append(check_record(f"loop_encloses:{identifier}", contained, True, contained,
                                       {"block_id": identifier, "method": "SIMPLE_RING_WITH_ANALYTIC_CLEARANCE"}))
    for connection in p["requirements"]["connection_points"]:
        distance = alignment_point_distance(primitives, connection["position"])
        margin = connection["max_centerline_distance_m"] - distance
        checks.append(check_record(f"connection:{connection['connection_id']}", distance,
                                   connection["max_centerline_distance_m"], margin >= -tol,
                                   {"connection_id": connection["connection_id"], "scope": "POINT_TO_SAME_ROUTE"}, margin))
    return {"status": "SATISFIED" if all(r["status"] == "SATISFIED" for r in checks) else "VIOLATED",
            "constraints": checks, "connected_component_count": 1 if adjacency else None,
            "cycle_count": 1 if closed and adjacency and simple else 0}

