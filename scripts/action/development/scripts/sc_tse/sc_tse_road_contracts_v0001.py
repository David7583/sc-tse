# ============================================================
# 文件名: sc_tse_road_contracts_v0001.py
# 中文名: 道路工程问题与配置契约
# 版本号: v0001
#
# 主层级: action
# 层级: development / sc_tse / phase4
# 脚本定位: road_problem_contract
#
# 职责说明:
# - 道路工程问题与配置契约
#
# 本脚本做什么:
# - 校验道路问题、配置、几何与稳定身份
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
# alias: sc_tse_road_contracts_v0001
# family: sc_tse_road_contracts
# role: road_problem_contract
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/sc_tse/sc_tse_road_contracts_v0001.py
# input:
#   - RoadProblem JSON and Phase4Config JSON
# output:
#   - validated RoadContext
# depends_on:
#   - Python stdlib
#   - Shapely
# used_by:
#   - run_sc_tse_phase4_v0001
#   - sc_tse_road_alignment_v0001
#   - sc_tse_road_constraints_v0001
#   - sc_tse_road_vertical_v0001
#   - sc_tse_road_metrics_v0001
# ============================================================

from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

from shapely.geometry import Polygon

DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "sc_tse_road_contracts"
SCRIPT_NAME = "sc_tse_road_contracts_v0001"
SCRIPT_VERSION = "v0001"
CONTRACT_VERSION = "sc-tse-road-v0.1.0"


# ============================================================
# 异常类型
# ============================================================

class RoadError(ValueError):
    """Classified input or configuration failure."""


class SearchLimit(RoadError):
    """Configured finite construction budget exhausted."""


class AlignmentUnavailable(RoadError):
    """This construction strategy cannot produce the requested alignment."""


# ============================================================
# 数据结构
# ============================================================

@dataclass(frozen=True)
class RoadContext:
    problem: dict
    config: dict
    problem_hash: str
    config_hash: str


# ============================================================
# 工具函数区
# ============================================================

def project_root() -> Path:
    for root in Path(__file__).resolve().parents:
        if (root / "PROGRAMMING_LANGUAGE_SCRIPT_FORMAT.md").is_file():
            return root
    raise RoadError("project root marker missing")


def canonical_hash(value: object) -> str:
    try:
        raw = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise RoadError("input is not finite JSON") from exc
    return hashlib.sha256(raw.encode(DEFAULT_ENCODING)).hexdigest()


def read_json(path: str | Path) -> dict:
    try:
        with Path(path).open(encoding="utf-8-sig") as stream:
            return json.load(stream, parse_constant=lambda x: (_ for _ in ()).throw(RoadError("nonfinite JSON")))
    except (OSError, ValueError) as exc:
        raise RoadError(f"cannot read JSON: {path}: {exc}") from exc


def keys(value: dict, required: set[str], optional: set[str], field: str) -> None:
    if not isinstance(value, dict) or required - value.keys() or value.keys() - required - optional:
        raise RoadError(f"{field}: missing or unknown fields")


def number(value: object, field: str, minimum: float = -math.inf, maximum: float = math.inf) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise RoadError(f"{field}: finite number required")
    if not minimum <= value <= maximum:
        raise RoadError(f"{field}: outside configured contract range")
    return float(value)


def identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RoadError(f"{field}: nonempty identifier required")
    return value


def point(value: object) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        raise RoadError("coordinate requires exactly XY; height is a separate field")
    return number(value[0], "x"), number(value[1], "y")


def ring(value: object, field: str, max_vertices: int) -> Polygon:
    if not isinstance(value, list) or not 4 <= len(value) <= max_vertices + 1:
        raise RoadError(f"{field}: invalid vertex count")
    vertices = [point(p) for p in value]
    if vertices[0] != vertices[-1] or any(a == b for a, b in zip(vertices, vertices[1:])):
        raise RoadError(f"{field}: explicitly closed nondegenerate ring required")
    shape = Polygon(vertices)
    if not shape.is_valid or shape.area <= 0:
        raise RoadError(f"{field}: invalid polygon; no implicit repair")
    return shape


# ============================================================
# 核心类
# ============================================================

def make_context(problem: dict, config: dict) -> RoadContext:
    validate_config(config)
    validate_problem(problem, config)
    return RoadContext(copy.deepcopy(problem), copy.deepcopy(config), canonical_hash(problem), canonical_hash(config))


# ============================================================
# Schema / 契约辅助函数
# ============================================================

def validate_config(c: dict) -> None:
    keys(c, {"config_version", "width_candidates_m", "setbacks", "turning", "vertical",
             "search", "numerics", "assumptions"}, set(), "config")
    if c["config_version"] != SCRIPT_VERSION:
        raise RoadError("unsupported configuration version")
    widths = c["width_candidates_m"]
    if not isinstance(widths, list) or not widths or len(widths) > 64:
        raise RoadError("width_candidates_m: 1..64 widths required")
    for w in widths:
        number(w, "width", 0.01)
    if len(set(widths)) != len(widths):
        raise RoadError("duplicate widths")
    keys(c["setbacks"], {"road_redline_m", "building_redline_m", "building_road_m"}, set(), "setbacks")
    for v in c["setbacks"].values():
        number(v, "setback", 0)
    keys(c["turning"], {"centerline_radius_m"}, set(), "turning")
    number(c["turning"]["centerline_radius_m"], "radius", max(widths) / 2 + 0.001)
    keys(c["vertical"], {"cross_slope_ratio", "min_cross_slope_ratio", "max_cross_slope_ratio",
                          "max_longitudinal_slope_ratio", "sample_step_m"}, set(), "vertical")
    v = c["vertical"]
    for k in ("cross_slope_ratio", "min_cross_slope_ratio", "max_cross_slope_ratio", "max_longitudinal_slope_ratio"):
        number(v[k], k, 0, 1)
    if v["min_cross_slope_ratio"] > v["max_cross_slope_ratio"]:
        raise RoadError("cross slope limits reversed")
    number(v["sample_step_m"], "sample step", 0.01)
    keys(c["search"], {"inset_offsets_m", "max_attempts", "max_vertices", "max_samples"}, set(), "search")
    for k in ("max_attempts", "max_vertices", "max_samples"):
        val = c["search"][k]
        if isinstance(val, bool) or not isinstance(val, int) or not 1 <= val <= 1000000:
            raise RoadError(f"{k}: bounded positive integer required")
    offsets = c["search"]["inset_offsets_m"]
    if not isinstance(offsets, list) or not offsets or len(offsets) > 256:
        raise RoadError("inset offsets required")
    for o in offsets:
        number(o, "inset offset", 0)
    keys(c["numerics"], {"linear_tolerance_m", "arc_chord_error_m"}, set(), "numerics")
    number(c["numerics"]["linear_tolerance_m"], "tolerance", 1e-12, 1e-4)
    number(c["numerics"]["arc_chord_error_m"], "chord error", 1e-6, 0.1)
    if not isinstance(c["assumptions"], dict):
        raise RoadError("assumptions must record configuration provenance")


def validate_problem(p: dict, c: dict) -> None:
    keys(p, {"schema_version", "problem_id", "coordinate_frame", "site", "buildings",
             "road_edge_overrides", "requirements", "design_elevation", "metadata"}, {"route"}, "problem")
    if p["schema_version"] != CONTRACT_VERSION:
        raise RoadError("unsupported RoadProblem version")
    identifier(p["problem_id"], "problem_id")
    if p["coordinate_frame"] != {"frame_id": "SC_TSE_CARTESIAN_RH", "handedness": "RIGHT",
                                  "z_axis": "UP", "length_unit": "m"}:
        raise RoadError("right handed metre XY/Z-up frame required")
    keys(p["site"], {"site_id", "boundary"}, set(), "site")
    identifier(p["site"]["site_id"], "site_id")
    cap = c["search"]["max_vertices"]
    ring(p["site"]["boundary"], "site", cap)
    if not isinstance(p["buildings"], list) or len(p["buildings"]) > cap:
        raise RoadError("invalid buildings count")
    ids = []
    for b in p["buildings"]:
        keys(b, {"block_id", "footprint"}, set(), "building")
        ids.append(identifier(b["block_id"], "block_id"))
        ring(b["footprint"], "building", cap)
    if len(set(ids)) != len(ids):
        raise RoadError("duplicate block identity")
    if not isinstance(p["road_edge_overrides"], list):
        raise RoadError("road_edge_overrides must be a list")
    edges = []
    for e in p["road_edge_overrides"]:
        keys(e, {"edge_index", "setback_m", "reason"}, set(), "edge override")
        i = e["edge_index"]
        if isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < len(p["site"]["boundary"]) - 1:
            raise RoadError("invalid redline edge index")
        edges.append(i)
        number(e["setback_m"], "edge setback", 0)
        identifier(e["reason"], "override reason")
    if len(edges) != len(set(edges)):
        raise RoadError("duplicate edge override")
    req = p["requirements"]
    keys(req, {"closed_loop", "enclosed_block_ids", "connection_points"}, set(), "requirements")
    if not isinstance(req["closed_loop"], bool):
        raise RoadError("closed_loop must be boolean")
    enclose = req["enclosed_block_ids"]
    if not isinstance(enclose, list) or any(not isinstance(i, str) for i in enclose):
        raise RoadError("enclosure IDs must be strings")
    if len(set(enclose)) != len(enclose) or set(enclose) - set(ids):
        raise RoadError("invalid enclosure references")
    if not isinstance(req["connection_points"], list) or len(req["connection_points"]) > cap:
        raise RoadError("invalid connection points")
    connection_ids = []
    for a in req["connection_points"]:
        keys(a, {"connection_id", "position", "max_centerline_distance_m"}, set(), "connection point")
        connection_ids.append(identifier(a["connection_id"], "connection_id"))
        point(a["position"])
        number(a["max_centerline_distance_m"], "connection tolerance", 0)
    if len(set(connection_ids)) != len(connection_ids):
        raise RoadError("duplicate connection identity")
    elevation = p["design_elevation"]
    if elevation is not None:
        keys(elevation, {"method", "datum_id", "origin_xy", "origin_z_m", "gradient_x", "gradient_y", "source_ref"},
             set(), "design elevation")
        if elevation["method"] != "DESIGN_PLANE":
            raise RoadError("unsupported elevation method")
        point(elevation["origin_xy"])
        for field in ("origin_z_m", "gradient_x", "gradient_y"):
            number(elevation[field], field)
        identifier(elevation["datum_id"], "datum_id")
        identifier(elevation["source_ref"], "source_ref")
    if "route" in p:
        r = p["route"]
        keys(r, {"waypoints", "closed", "width_m", "centerline_radius_m"}, set(), "route")
        if not isinstance(r["closed"], bool) or not isinstance(r["waypoints"], list) or not 2 <= len(r["waypoints"]) <= cap:
            raise RoadError("invalid route")
        for xy in r["waypoints"]:
            point(xy)
        if any(a == b for a, b in zip(r["waypoints"], r["waypoints"][1:])) or r["waypoints"][0] == r["waypoints"][-1]:
            raise RoadError("route points must be nonduplicated; closure is implicit")
        number(r["width_m"], "route width", min(c["width_candidates_m"]), max(c["width_candidates_m"]))
        number(r["centerline_radius_m"], "route radius", r["width_m"] / 2 + 0.001)
    if not isinstance(p["metadata"], dict):
        raise RoadError("metadata must be an object")
    canonical_hash(p)

