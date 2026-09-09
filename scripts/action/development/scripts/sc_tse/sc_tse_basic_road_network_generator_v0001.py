# ============================================================
# 文件名: sc_tse_basic_road_network_generator_v0001.py
# 中文名: SC-TSE 基础道路网络生成器
# 版本号: v0001
#
# 主层级: action
# 层级: development / sc_tse / road_network_provider
# 脚本定位: 将已准入 Phase 2 路径拓扑化为统一 RoadGraph
#
# 职责说明:
# - 复用 Phase 2 FreeSpace 与 Grid A*，合并路径并生成可追溯 RoadGraph
#
# 本脚本做什么:
# - 校验跨阶段身份、复用已准入路径、补算显式网络连接并输出图拓扑
#
# 本脚本不做什么:
# - 不移动 Block，不修改 Core，不决定工作流完成状态，不执行道路工程校核
#
# 制度边界声明:
# - 输入只读；无数据库、网络或隐式文件写入
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: sc_tse_basic_road_network_generator_v0001
# family: sc_tse_basic_road_network_generator
# role: basic_road_network_provider
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/sc_tse/sc_tse_basic_road_network_generator_v0001.py
# input:
#   - admitted Phase 2 problem/result, RoadNetworkRequest and config
# output:
#   - RoadGraphRun with nodes, edges, polylines, served_gates, components and cycles
# depends_on:
#   - sc_tse_connectivity_contracts_v0001
#   - sc_tse_connectivity_engine_v0001
#   - jsonschema
#   - Shapely 2.x
# used_by:
#   - run_sc_tse_orchestrator_v0004
#   - test_sc_tse_basic_road_network_v0001
# ============================================================

from __future__ import annotations

import hashlib
import json
import math
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import jsonschema
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from sc_tse_connectivity_contracts_v0001 import ConnectivityProblem, load_phase2_config
from sc_tse_connectivity_engine_v0001 import ConnectivityEvaluator

DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "sc_tse_basic_road_network_generator"
SCRIPT_NAME = "sc_tse_basic_road_network_generator_v0001"
SCRIPT_VERSION = "v0001"
RESULT_VERSION = "sc-tse-road-graph-result-v0.1.0"


# ============================================================
# 异常类型
# ============================================================

class BasicRoadNetworkError(RuntimeError):
    """Base error for the external BasicRoadNetwork Provider."""


class BasicRoadNetworkInputError(BasicRoadNetworkError):
    """Raised when admitted inputs are inconsistent or incomplete."""


# ============================================================
# 工具函数区
# ============================================================

def _hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode(DEFAULT_ENCODING)).hexdigest()


def _schema(filename: str) -> dict[str, Any]:
    local = Path(__file__).resolve().parent / "schemas" / filename
    active = next(
        parent / "scripts/action/development/scripts/sc_tse/schemas" / filename
        for parent in Path(__file__).resolve().parents
        if (parent / "PROGRAMMING_LANGUAGE_SCRIPT_FORMAT.md").is_file()
    )
    path = local if local.is_file() else active
    return json.loads(path.read_text(encoding=DEFAULT_ENCODING))


def _validate(value: Mapping[str, Any], filename: str) -> None:
    jsonschema.Draft202012Validator(_schema(filename)).validate(dict(value))


def _problem(value: Mapping[str, Any]) -> ConnectivityProblem:
    blocks = {str(item["block_id"]): item for item in value["blocks"]}
    gates: dict[str, Mapping[str, Any]] = {}
    owners: dict[str, str] = {}
    for block_id, block in blocks.items():
        for gate in block["connection_gates"]:
            gate_id = str(gate["gate_id"])
            gates[gate_id] = gate
            owners[gate_id] = block_id
    buffers = {str(item["block_id"]): float(item["buffer_m"]) for item in value["obstacle_buffers"]}
    return ConnectivityProblem(str(value["problem_id"]), value, blocks, gates, owners, buffers)


def _gateway_map(result: Mapping[str, Any]) -> dict[str, list[float]]:
    return {
        str(item["gate_id"]): [float(item["gateway"][0]), float(item["gateway"][1])]
        for item in result["evidence"]
        if item.get("evidence_type") == "GATEWAY_SEARCH" and item.get("status") == "FOUND"
    }


def _deduplicate(points: Sequence[Sequence[float]], tolerance: float) -> list[list[float]]:
    result: list[list[float]] = []
    for value in points:
        point = [round(float(value[0]) / tolerance) * tolerance, round(float(value[1]) / tolerance) * tolerance]
        if not result or math.dist(point, result[-1]) > tolerance / 2:
            result.append([round(point[0], 9), round(point[1], 9)])
    return result


def _remove_collinear(points: Sequence[Sequence[float]], tolerance: float) -> list[list[float]]:
    reduced: list[list[float]] = []
    for point in points:
        reduced.append(list(point))
        while len(reduced) >= 3:
            first, middle, last = reduced[-3:]
            cross = (middle[0] - first[0]) * (last[1] - middle[1]) - (middle[1] - first[1]) * (last[0] - middle[0])
            if abs(cross) > tolerance:
                break
            reduced.pop(-2)
    return reduced


def _components(adjacency: Mapping[tuple[float, float], set[tuple[float, float]]]) -> list[list[tuple[float, float]]]:
    unseen = set(adjacency)
    components = []
    while unseen:
        seed = min(unseen)
        stack = [seed]
        current = []
        unseen.remove(seed)
        while stack:
            node = stack.pop()
            current.append(node)
            for neighbor in sorted(adjacency[node]):
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    stack.append(neighbor)
        components.append(sorted(current))
    return components


def _network_polylines(
    segments: Mapping[tuple[tuple[float, float], tuple[float, float]], set[str]],
    adjacency: Mapping[tuple[float, float], set[tuple[float, float]]],
    path_roles: Mapping[str, str],
    path_strategies: Mapping[str, str],
) -> list[dict[str, Any]]:
    unused = set(segments)
    chains: list[dict[str, Any]] = []

    def edge(first: tuple[float, float], second: tuple[float, float]) -> tuple[tuple[float, float], tuple[float, float]]:
        return (first, second) if first < second else (second, first)

    def walk(start: tuple[float, float], neighbor: tuple[float, float]) -> None:
        coordinates = [start, neighbor]
        source_ids = set(segments[edge(start, neighbor)])
        unused.remove(edge(start, neighbor))
        previous, current = start, neighbor
        while len(adjacency[current]) == 2:
            candidates = sorted(item for item in adjacency[current] if item != previous and edge(current, item) in unused)
            if not candidates:
                break
            following = candidates[0]
            source_ids.update(segments[edge(current, following)])
            unused.remove(edge(current, following))
            coordinates.append(following)
            previous, current = current, following
        closed = coordinates[0] == coordinates[-1]
        source_strategies = sorted({path_strategies[identity] for identity in source_ids})
        roles = {path_roles[identity] for identity in source_ids}
        payload = {
            "coordinates": [list(item) for item in coordinates],
            "source_connection_ids": sorted(source_ids),
            "source_strategies": source_strategies,
            "role": "REDUNDANCY" if "REDUNDANCY" in roles else "PRIMARY",
            "closed": closed,
        }
        chains.append({"polyline_id": f"RP-{_hash(payload)[:16]}", **payload})

    for start in sorted(point for point in adjacency if len(adjacency[point]) != 2):
        for neighbor in sorted(adjacency[start]):
            if edge(start, neighbor) in unused:
                walk(start, neighbor)
    while unused:
        first, second = min(unused)
        walk(first, second)
    return chains


def _topologize(paths: Sequence[Mapping[str, Any]], tolerance: float) -> dict[str, Any]:
    merged = unary_union([LineString(item["coordinates"]) for item in paths])
    lines = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
    segments: dict[tuple[tuple[float, float], tuple[float, float]], set[str]] = defaultdict(set)
    for line in lines:
        coordinates = _deduplicate(list(line.coords), tolerance)
        for first, second in zip(coordinates, coordinates[1:]):
            a, b = tuple(first), tuple(second)
            key = (a, b) if a < b else (b, a)
            segments[key].update(
                item["connection_id"] for item in paths
                if LineString(item["coordinates"]).buffer(tolerance).covers(Point((a[0] + b[0]) / 2, (a[1] + b[1]) / 2))
            )
    adjacency: dict[tuple[float, float], set[tuple[float, float]]] = defaultdict(set)
    for first, second in segments:
        adjacency[first].add(second)
        adjacency[second].add(first)
    components = _components(adjacency)
    node_ids = {point: f"RN-{_hash(point)[:16]}" for point in sorted(adjacency)}
    nodes = [
        {"node_id": node_ids[point], "position": list(point), "degree": len(adjacency[point])}
        for point in sorted(adjacency)
    ]
    edges = [
        {
            "edge_id": f"RE-{_hash([first, second])[:16]}",
            "source_node": node_ids[first],
            "target_node": node_ids[second],
            "coordinates": [list(first), list(second)],
            "length_m": round(math.dist(first, second), 9),
            "source_connection_ids": sorted(source_ids),
        }
        for (first, second), source_ids in sorted(segments.items())
    ]
    cycle_count = len(edges) - len(nodes) + len(components)
    return {
        "nodes": nodes,
        "edges": edges,
        "polylines": _network_polylines(
            segments,
            adjacency,
            {item["connection_id"]: item["role"] for item in paths},
            {item["connection_id"]: item["strategy"] for item in paths},
        ),
        "connected_components": [[node_ids[point] for point in component] for component in components],
        "connected_component_count": len(components),
        "cycle_count": cycle_count,
    }


# ============================================================
# 核心类
# ============================================================

def generate_basic_road_network(
    phase2_problem: dict[str, Any],
    phase2_result: dict[str, Any],
    request: dict[str, Any],
    config: dict[str, Any],
    *,
    phase2_config_path: str | Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Generate a RoadGraph without changing Phase 1/2/4 Core responsibilities."""
    started = time.perf_counter()
    _validate(phase2_problem, "sc_tse_connectivity_problem_v0001.schema.json")
    _validate(phase2_result, "sc_tse_connectivity_result_v0001.schema.json")
    _validate(request, "sc_tse_road_graph_request_v0001.schema.json")
    _validate(config, "sc_tse_road_graph_config_v0001.schema.json")
    if phase2_result.get("execution_status") != "COMPLETED" or phase2_result.get("solve_status") != "FEASIBLE":
        raise BasicRoadNetworkInputError("RoadGraph generation requires an admitted feasible Phase 2 result")
    if phase2_problem["problem_id"] != phase2_result["problem_id"] or request["problem_id"] != phase2_problem["problem_id"]:
        raise BasicRoadNetworkInputError("problem identity differs across RoadGraph inputs")
    phase2_connections = {item["connection_id"]: item for item in phase2_result["connection_results"]}
    gate_positions = {
        gate["gate_id"]: list(gate["position"])
        for block in phase2_problem["blocks"]
        for gate in block["connection_gates"]
    }
    gateways = _gateway_map(phase2_result)
    entrances = {item["site_entrance_id"]: item for item in request["site_entrances"]}
    site_boundary = Polygon(phase2_problem["site"]["geometry"]["coordinates"]).boundary
    tolerance = float(config["snap_tolerance_m"])
    for entrance in entrances.values():
        if site_boundary.distance(Point(entrance["position"])) > tolerance:
            raise BasicRoadNetworkInputError(f"{entrance['site_entrance_id']}: SiteEntrance must lie on site boundary")

    def endpoint(reference: Mapping[str, Any]) -> tuple[list[float], list[float]]:
        if reference["kind"] == "BUILDING_GATE":
            identity = reference["id"]
            if identity not in gate_positions or identity not in gateways:
                raise BasicRoadNetworkInputError(f"unknown or unserved BuildingGate: {identity}")
            return gate_positions[identity], gateways[identity]
        entrance = entrances.get(reference["id"])
        if entrance is None:
            raise BasicRoadNetworkInputError(f"unknown SiteEntrance: {reference['id']}")
        return list(entrance["position"]), list(entrance["road_connection_position"])

    connectivity_config = load_phase2_config(phase2_config_path)
    evaluator = ConnectivityEvaluator(connectivity_config)
    connectivity_problem = _problem(phase2_problem)
    _, _, free_space = evaluator._free_space(connectivity_problem)
    paths: list[dict[str, Any]] = []
    access_map: dict[str, dict[str, Any]] = {}
    failures = []
    for connection in request["network_connections"]:
        source_gate, source_network = endpoint(connection["source"])
        target_gate, target_network = endpoint(connection["target"])
        for reference, gate_point, network_point in (
            (connection["source"], source_gate, source_network),
            (connection["target"], target_gate, target_network),
        ):
            access_map[reference["id"]] = {
                "gate_id": reference["id"],
                "gate_kind": reference["kind"],
                "gate_position": gate_point,
                "network_position": network_point,
                "coordinates": [gate_point, network_point],
                "source_node": SCRIPT_NAME,
            }
        reused = connection.get("phase2_connection_id")
        if reused:
            source = phase2_connections.get(reused)
            if source is None or source.get("status") != "CONNECTED" or source.get("path") is None:
                failures.append({"connection_id": connection["connection_id"], "reason": "PHASE2_PATH_NOT_ADMITTED"})
                continue
            coordinates = source["path"]["coordinates"]
            strategy = "REUSED_PHASE2_PATH"
        else:
            search_space = free_space
            if connection["role"] == "REDUNDANCY" and paths:
                occupied = unary_union([LineString(item["coordinates"]) for item in paths])
                clearance = float(config["redundancy_clearance_m"])
                endpoint_relief = Point(source_network).buffer(clearance * 1.5).union(Point(target_network).buffer(clearance * 1.5))
                search_space = free_space.difference(occupied.buffer(clearance)).union(free_space.intersection(endpoint_relief))
            outcome = evaluator._grid_a_star(
                tuple(source_network), tuple(target_network), search_space,
                Polygon(phase2_problem["site"]["geometry"]["coordinates"]).bounds,
                phase2_problem["solver_config"],
            )
            if outcome.status != "CONNECTED":
                failures.append({"connection_id": connection["connection_id"], "reason": outcome.status})
                continue
            coordinates = [list(item) for item in outcome.coordinates]
            strategy = "PHASE2_GRID_A_STAR_REUSE"
        paths.append({
            "connection_id": connection["connection_id"],
            "coordinates": _deduplicate(coordinates, tolerance),
            "role": connection["role"],
            "strategy": strategy,
        })
    required_ids = {item["connection_id"] for item in request["network_connections"] if item["required"]}
    generated_ids = {item["connection_id"] for item in paths}
    missing = sorted(required_ids - generated_ids)
    graph = _topologize(paths, tolerance) if paths else {
        "nodes": [], "edges": [], "polylines": [], "connected_components": [],
        "connected_component_count": 0, "cycle_count": 0,
    }
    cycle_satisfied = not request["requirements"]["minimum_cycle_count"] or graph["cycle_count"] >= request["requirements"]["minimum_cycle_count"]
    feasible = not missing and graph["connected_component_count"] == 1 and cycle_satisfied
    result = {
        "schema_version": RESULT_VERSION,
        "record_type": "RoadGraphRun",
        "execution_status": "COMPLETED",
        "solve_status": "FEASIBLE" if feasible else "UNRESOLVED",
        "hard_constraint_status": "SATISFIED" if feasible else "VIOLATED",
        "problem_id": phase2_problem["problem_id"],
        "road_graph_id": "ROAD-GRAPH-" + _hash([phase2_problem["problem_id"], graph])[:20],
        "road_graph": graph,
        "served_gates": [access_map[key] for key in sorted(access_map)],
        "failure_data": failures + [{"connection_id": item, "reason": "MISSING_REQUIRED_CONNECTION"} for item in missing],
        "source_refs": {
            "phase2_problem_hash": _hash(phase2_problem),
            "phase2_result_hash": _hash(phase2_result),
            "request_hash": _hash(request),
            "config_hash": _hash(config),
            "generator": SCRIPT_NAME,
        },
        "observability": {"total_ms": (time.perf_counter() - started) * 1000.0, "path_count": len(paths)},
    }
    _validate(result, "sc_tse_road_graph_result_v0001.schema.json")
    if dry_run:
        return {"schema_version": RESULT_VERSION, "record_type": "RoadGraphDryRun", "status": "dry_run", "problem_id": result["problem_id"]}
    return result


# ============================================================
# CLI / main 接口区
# ============================================================

# Import-only Provider; the workflow runner owns file resolution and persistence.
