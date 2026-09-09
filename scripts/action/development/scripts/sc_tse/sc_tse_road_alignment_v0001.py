# ============================================================
# 文件名: sc_tse_road_alignment_v0001.py
# 中文名: 道路直线圆弧路线构造与距离计算
# 版本号: v0001
#
# 主层级: action
# 层级: development / sc_tse / phase4
# 脚本定位: road_alignment_strategy
#
# 职责说明:
# - 道路直线圆弧路线构造与距离计算
#
# 本脚本做什么:
# - 有限内缩候选或显式折线转为相切直线圆弧路线与解析距离
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
# alias: sc_tse_road_alignment_v0001
# family: sc_tse_road_alignment
# role: road_alignment_strategy
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/sc_tse/sc_tse_road_alignment_v0001.py
# input:
#   - RoadContext or road waypoints
# output:
#   - alignment primitives and exact planar clearance
# depends_on:
#   - sc_tse_road_contracts_v0001
#   - Shapely
#   - Python stdlib
# used_by:
#   - run_sc_tse_phase4_v0001
#   - sc_tse_road_constraints_v0001
#   - sc_tse_road_vertical_v0001
# ============================================================

from __future__ import annotations

import math

from shapely.geometry import LineString, Point, Polygon

from sc_tse_road_contracts_v0001 import AlignmentUnavailable, SearchLimit

DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "sc_tse_road_alignment"
SCRIPT_NAME = "sc_tse_road_alignment_v0001"
SCRIPT_VERSION = "v0001"


# ============================================================
# 数据结构
# ============================================================

# AlignmentPrimitive v0001: LINE(start,end,length_m) or
# ARC(start,end,center,radius_m,start_angle_rad,sweep_rad,length_m).
# All angles are mathematical radians; positive sweep is counterclockwise.


# ============================================================
# 工具函数区
# ============================================================

def add(a, b):
    return (a[0] + b[0], a[1] + b[1])


def scale(a, k):
    return (a[0] * k, a[1] * k)


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


def norm(a):
    return math.hypot(*a)


def unit(a):
    size = norm(a)
    if size == 0:
        raise AlignmentUnavailable("zero length segment")
    return scale(a, 1 / size)


def left(a):
    return (-a[1], a[0])


def angle_on_arc(angle: float, arc: dict) -> bool:
    delta = (angle - arc["start_angle_rad"]) % math.tau if arc["sweep_rad"] > 0 else (arc["start_angle_rad"] - angle) % math.tau
    return delta <= abs(arc["sweep_rad"]) + 1e-12 or abs(delta - math.tau) < 1e-12


def point_at(primitive: dict, fraction: float) -> tuple[tuple[float, float], tuple[float, float]]:
    if primitive["kind"] == "LINE":
        direction = sub(primitive["end"], primitive["start"])
        return add(primitive["start"], scale(direction, fraction)), unit(direction)
    angle = primitive["start_angle_rad"] + primitive["sweep_rad"] * fraction
    sign = 1 if primitive["sweep_rad"] > 0 else -1
    return add(primitive["center"], (primitive["radius_m"] * math.cos(angle), primitive["radius_m"] * math.sin(angle))), (-sign * math.sin(angle), sign * math.cos(angle))


def point_arc_distance(xy, arc: dict) -> float:
    vector = sub(xy, arc["center"])
    if angle_on_arc(math.atan2(vector[1], vector[0]), arc):
        return abs(norm(vector) - arc["radius_m"])
    return min(norm(sub(xy, arc["start"])), norm(sub(xy, arc["end"])))


def primitive_segment_distance(p: dict, a, b) -> float:
    edge = LineString([a, b])
    if p["kind"] == "LINE":
        return LineString([p["start"], p["end"]]).distance(edge)
    center, radius = p["center"], p["radius_m"]
    candidates = [Point(p["start"]).distance(edge), Point(p["end"]).distance(edge),
                  point_arc_distance(a, p), point_arc_distance(b, p)]
    direction = sub(b, a)
    length = norm(direction)
    if length == 0:
        return point_arc_distance(a, p)
    axis = scale(direction, 1 / length)
    # Interior stationary distances from the circle to the infinite support line.
    for sign in (-1, 1):
        radial = scale(left(axis), sign)
        q = add(center, scale(radial, radius))
        projection = dot(sub(q, a), axis)
        if 0 <= projection <= length and angle_on_arc(math.atan2(radial[1], radial[0]), p):
            candidates.append(Point(q).distance(edge))
    # Circle / segment intersections are possible minima not covered by stationary distances.
    rel = sub(a, center)
    linear = dot(rel, axis)
    disc = linear * linear - dot(rel, rel) + radius * radius
    if disc >= 0:
        for t in (-linear - math.sqrt(disc), -linear + math.sqrt(disc)):
            if 0 <= t <= length:
                q = add(a, scale(axis, t))
                if angle_on_arc(math.atan2(q[1] - center[1], q[0] - center[0]), p):
                    return 0.0
    return min(candidates)


def alignment_distance(primitives: list[dict], coordinates: list) -> float:
    return min(primitive_segment_distance(p, a, b) for p in primitives for a, b in zip(coordinates, coordinates[1:]))


def alignment_point_distance(primitives: list[dict], xy) -> float:
    return min(Point(xy).distance(LineString([p["start"], p["end"]])) if p["kind"] == "LINE"
               else point_arc_distance(xy, p) for p in primitives)


def sample_alignment(primitives: list[dict], chord_error: float, step: float, max_samples: int) -> list[dict]:
    samples, station = [], 0.0
    for index, p in enumerate(primitives):
        count = max(1, math.ceil(p["length_m"] / step))
        if p["kind"] == "ARC":
            angle_step = 2 * math.acos(max(-1, 1 - min(chord_error / p["radius_m"], 1)))
            count = max(count, math.ceil(abs(p["sweep_rad"]) / angle_step))
        if len(samples) + count + 1 > max_samples:
            raise SearchLimit("alignment sampling budget exhausted")
        for j in range(count):
            xy, tangent = point_at(p, j / count)
            samples.append({"station_m": station + p["length_m"] * j / count,
                            "xy": list(xy), "tangent": list(tangent), "primitive_index": index})
        station += p["length_m"]
    xy, tangent = point_at(primitives[-1], 1)
    samples.append({"station_m": station, "xy": list(xy), "tangent": list(tangent), "primitive_index": len(primitives) - 1})
    return samples


def edge_setbacks(problem: dict, config: dict) -> list[float]:
    result = [config["setbacks"]["road_redline_m"]] * (len(problem["site"]["boundary"]) - 1)
    for override in problem["road_edge_overrides"]:
        result[override["edge_index"]] = override["setback_m"]
    return result


# ============================================================
# 核心类
# ============================================================

def inset_waypoints(boundary: list, distances: list[float], tolerance: float) -> list:
    points = boundary[:-1]
    sign = 1 if sum(cross(a, b) for a, b in zip(boundary, boundary[1:])) > 0 else -1
    lines = []
    for i, a in enumerate(points):
        direction = unit(sub(points[(i + 1) % len(points)], a))
        lines.append((add(a, scale(left(direction), sign * distances[i])), direction))
    result = []
    for i, (q, v) in enumerate(lines):
        p, u = lines[i - 1]
        det = cross(u, v)
        if abs(det) < tolerance:
            raise AlignmentUnavailable("parallel consecutive redline edges are unsupported by miter strategy")
        result.append(list(add(p, scale(u, cross(sub(q, p), v) / det))))
    polygon = Polygon(result)
    if not polygon.is_valid or polygon.area <= tolerance or not Polygon(boundary).buffer(tolerance).covers(polygon):
        raise AlignmentUnavailable("inset collapses or leaves site; baseline cannot construct this ring")
    return result


def fillet_alignment(waypoints: list, closed: bool, radius: float, tolerance: float) -> list[dict]:
    points = [tuple(p) for p in waypoints]
    if closed and len(points) < 3:
        raise AlignmentUnavailable("a closed alignment requires at least three vertices")
    corners = []
    for i, p in enumerate(points):
        if not closed and i in (0, len(points) - 1):
            corners.append((p, p, 0.0, None))
            continue
        incoming = unit(sub(p, points[i - 1]))
        outgoing = unit(sub(points[(i + 1) % len(points)], p))
        theta = math.atan2(cross(incoming, outgoing), dot(incoming, outgoing))
        if abs(theta) < tolerance:
            corners.append((p, p, 0.0, None))
            continue
        if math.pi - abs(theta) < tolerance:
            raise AlignmentUnavailable("reversal cannot be filleted")
        trim = radius * math.tan(abs(theta) / 2)
        start, end = sub(p, scale(incoming, trim)), add(p, scale(outgoing, trim))
        center = add(start, scale(left(incoming), radius if theta > 0 else -radius))
        arc = {"kind": "ARC", "start": list(start), "end": list(end), "center": list(center),
               "radius_m": radius, "start_angle_rad": math.atan2(start[1] - center[1], start[0] - center[0]),
               "sweep_rad": theta, "length_m": radius * abs(theta)}
        corners.append((start, end, trim, arc))
    edge_count = len(points) if closed else len(points) - 1
    for i in range(edge_count):
        nxt = (i + 1) % len(points)
        if corners[i][2] + corners[nxt][2] > norm(sub(points[nxt], points[i])) + tolerance:
            raise AlignmentUnavailable(f"turning tangencies overlap on segment {i}; radius cannot fit")
    primitives = []
    for i in range(len(points)):
        if corners[i][3] is not None:
            primitives.append(corners[i][3])
        if i < edge_count:
            end = corners[(i + 1) % len(points)][0]
            start = corners[i][1]
            length = norm(sub(end, start))
            if length > tolerance:
                primitives.append({"kind": "LINE", "start": list(start), "end": list(end), "length_m": length})
    if not primitives:
        raise AlignmentUnavailable("empty alignment")
    return primitives

