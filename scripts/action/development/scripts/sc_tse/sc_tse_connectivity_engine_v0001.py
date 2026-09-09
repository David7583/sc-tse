# ============================================================
# 文件名: sc_tse_connectivity_engine_v0001.py
# 中文名: SC-TSE Phase 2 基础连通性计算脚本
# 版本号: v0001
#
# 主层级: action
# 层级: development / sc_tse / phase2_connectivity_engine
# 脚本定位: 当前 Layout 上 Gate、Gateway、FreeSpace 与基础 Path 的只读计算内核
#
# 职责说明:
# - 从显式 Buffer 构造二维 FreeSpace 并搜索 Gate 对应 Gateway
# - 使用单一确定性四邻域 Grid A* 评估 ConnectionRequirement
#
# 本脚本做什么:
# - 输出逐连接结果、Level 1 拓扑、Diagnostics、Evidence 和 Failure Data
# - 严格区分当前离散策略未找到路径与已证明连续空间无解
#
# 本脚本不做什么:
# - 不移动 Block/Gate，不修改 Problem，不决定下一轮业务动作
# - 不执行道路工程、自动重布局、跨 Solver 调度或反馈编排
#
# 制度边界声明:
# - Gate 是输入对象边界上的稳定语义点；Gateway 是可重算的求解派生点
# - Grid 图穷尽只证明当前离散图无路径，整体 Solve Status 仍为 UNRESOLVED
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: sc_tse_connectivity_engine_v0001
# family: sc_tse_connectivity_engine
# role: phase2_basic_connectivity_computation
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/sc_tse/sc_tse_connectivity_engine_v0001.py
# input:
#   - validated Phase2Config and ConnectivityProblem
# output:
#   - ConnectivityOutcome with structured connection results and failure data
# depends_on:
#   - Python stdlib: dataclasses, heapq, math, typing
#   - Shapely 2.x
#   - sc_tse_geometry_v0001
#   - sc_tse_connectivity_contracts_v0001
# used_by:
#   - run_sc_tse_phase2_v0001
#   - test_sc_tse_phase2_v0001
# ============================================================

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from shapely.geometry import LineString, Point
from shapely.ops import unary_union

from sc_tse_connectivity_contracts_v0001 import ConnectivityProblem, Phase2Config
from sc_tse_geometry_v0001 import GeometryEvaluator, ScTseGeometryError


DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "sc_tse_connectivity_engine"
SCRIPT_NAME = "sc_tse_connectivity_engine_v0001"
SCRIPT_VERSION = "v0001"


# ============================================================
# 异常类型
# ============================================================

class ScTseConnectivityEngineError(RuntimeError):
    """Base error for Phase 2 basic connectivity computation."""


class ConnectivityGeometryError(ScTseConnectivityEngineError):
    """Raised when Gate, FreeSpace or grid geometry cannot be evaluated safely."""


# ============================================================
# 数据结构
# ============================================================

@dataclass(frozen=True)
class GatewaySearchResult:
    gate_id: str
    status: str
    point: tuple[float, float] | None
    direction: tuple[float, float] | None
    searched_distance_m: float
    probes: int


@dataclass(frozen=True)
class PathSearchResult:
    status: str
    coordinates: tuple[tuple[float, float], ...]
    length_m: float | None
    grid_expansions: int
    grid_cells: int
    proof_capability: str


@dataclass(frozen=True)
class ConnectivityOutcome:
    execution_status: str
    solve_status: str
    solution_quality: str
    connectivity_status: str
    connection_results: tuple[Mapping[str, Any], ...]
    topology: Mapping[str, Any]
    failure_data: tuple[Mapping[str, Any], ...]
    diagnostics: Mapping[str, Any]
    evidence: tuple[Mapping[str, Any], ...]
    termination_reason: str
    grid_expansions: int
    proof_capability: str


# ============================================================
# 工具函数区
# ============================================================

def _point_tuple(value: Sequence[Any]) -> tuple[float, float]:
    return float(value[0]), float(value[1])


def _json_point(value: tuple[float, float]) -> list[float]:
    return [round(value[0], 12), round(value[1], 12)]


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _probe_distances(initial: float, step: float, maximum: float, tolerance: float) -> Iterable[float]:
    value = initial
    while value <= maximum + tolerance:
        yield min(value, maximum)
        value += step


def _candidate_directions(preferred: tuple[float, float]) -> Iterable[tuple[float, float]]:
    """Yield deterministic candidate directions without assuming one polygon normal."""
    base_angle = math.atan2(preferred[1], preferred[0])
    yield math.cos(base_angle), math.sin(base_angle)
    for offset_index in range(1, 37):
        offset = math.radians(offset_index * 5.0)
        for angle in (base_angle + offset, base_angle - offset):
            yield math.cos(angle), math.sin(angle)


def _polyline_length(coordinates: Sequence[tuple[float, float]]) -> float:
    return sum(_distance(a, b) for a, b in zip(coordinates, coordinates[1:]))


def _topology(problem: ConnectivityProblem) -> dict[str, Any]:
    nodes = [
        {"block_id": block_id, "gate_ids": sorted(str(item["gate_id"]) for item in block["connection_gates"])}
        for block_id, block in sorted(problem.blocks.items())
    ]
    edges = [
        {
            "connection_id": str(item["connection_id"]),
            "source_block": str(item["source_block"]),
            "target_block": str(item["target_block"]),
            "required": bool(item["required"]),
        }
        for item in problem.payload["connection_requirements"]
    ]
    return {"level": "LEVEL_1_EXPLICIT_REQUIREMENTS", "nodes": nodes, "edges": edges}


# ============================================================
# 默认映射
# ============================================================

PATH_STRATEGY = "GRID_A_STAR_4_NEIGHBOR"
NEIGHBOR_OFFSETS = ((1, 0), (0, 1), (-1, 0), (0, -1))


# ============================================================
# 核心类
# ============================================================

class ConnectivityEvaluator:
    """Read-only evaluator for basic 2D connectivity on the supplied Layout."""

    def __init__(self, config: Phase2Config) -> None:
        self.config = config
        self.geometry = GeometryEvaluator(config.tolerance)

    def evaluate(self, problem: ConnectivityProblem) -> ConnectivityOutcome:
        site_shape, block_shapes, free_space = self._free_space(problem)
        gateways = {
            gate_id: self._find_gateway(
                gate_id,
                gate,
                block_shapes[problem.gate_owners[gate_id]],
                free_space,
                problem.payload["solver_config"],
            )
            for gate_id, gate in sorted(problem.gates.items())
        }
        connection_results: list[Mapping[str, Any]] = []
        failures: list[Mapping[str, Any]] = []
        evidence: list[Mapping[str, Any]] = []
        total_expansions = 0
        interrupted = False
        graph_exhausted = False

        for connection in problem.payload["connection_requirements"]:
            source = gateways[str(connection["source_gate"])]
            target = gateways[str(connection["target_gate"])]
            result, failure, path_evidence, path = self._evaluate_connection(
                connection,
                source,
                target,
                free_space,
                site_shape.bounds,
                problem.payload["solver_config"],
            )
            connection_results.append(result)
            if failure is not None:
                failures.append(failure)
            evidence.extend(path_evidence)
            if path is not None:
                total_expansions += path.grid_expansions
                interrupted = interrupted or path.status == "SEARCH_BUDGET_EXHAUSTED"
                graph_exhausted = graph_exhausted or path.proof_capability == "GRID_GRAPH_EXHAUSTION"

        required_results = [
            result for result in connection_results if bool(result["required"])
        ]
        satisfied = all(result["status"] == "CONNECTED" for result in required_results)
        if satisfied:
            execution_status = "COMPLETED"
            solve_status = "FEASIBLE"
            quality = "GOOD"
            connectivity_status = "SATISFIED"
            termination = "ALL_REQUIRED_CONNECTIONS_CONNECTED"
        elif interrupted:
            execution_status = "INTERRUPTED"
            solve_status = "UNRESOLVED"
            quality = "UNKNOWN"
            connectivity_status = "NOT_SATISFIED"
            termination = "SEARCH_BUDGET_EXHAUSTED"
        else:
            execution_status = "COMPLETED"
            solve_status = "UNRESOLVED"
            quality = "UNKNOWN"
            connectivity_status = "NOT_SATISFIED"
            termination = "REQUIRED_CONNECTION_NOT_FOUND_BY_CURRENT_STRATEGY"

        gateway_evidence = [
            {
                "evidence_type": "GATEWAY_SEARCH",
                "gate_id": gate_id,
                "status": value.status,
                "gateway": _json_point(value.point) if value.point is not None else None,
                "direction": _json_point(value.direction) if value.direction is not None else None,
                "searched_distance_m": value.searched_distance_m,
                "probes": value.probes,
            }
            for gate_id, value in sorted(gateways.items())
        ]
        return ConnectivityOutcome(
            execution_status=execution_status,
            solve_status=solve_status,
            solution_quality=quality,
            connectivity_status=connectivity_status,
            connection_results=tuple(connection_results),
            topology=_topology(problem),
            failure_data=tuple(failures),
            diagnostics={
                "free_space_area_m2": round(float(free_space.area), 12),
                "gateway_count": len(gateways),
                "connected_required_count": sum(
                    result["status"] == "CONNECTED" for result in required_results
                ),
                "required_connection_count": len(required_results),
                "scope": "BASIC_2D_CONNECTIVITY_ONLY",
            },
            evidence=tuple(gateway_evidence + evidence),
            termination_reason=termination,
            grid_expansions=total_expansions,
            proof_capability="GRID_GRAPH_EXHAUSTION" if graph_exhausted else "NONE",
        )

    def _free_space(self, problem: ConnectivityProblem) -> tuple[Any, dict[str, Any], Any]:
        try:
            site = self.geometry.geometry(problem.payload["site"]["geometry"], str(problem.payload["site"]["site_id"]))
            blocks = {
                block_id: self.geometry.geometry(block["geometry"], block_id)
                for block_id, block in problem.blocks.items()
            }
        except ScTseGeometryError:
            raise
        for block_id, shape in blocks.items():
            if not site.covers(shape):
                raise ConnectivityGeometryError(f"{block_id}: block geometry must be covered by site")
        buffered = [
            shape.buffer(problem.obstacle_buffers[block_id], join_style="mitre")
            for block_id, shape in blocks.items()
        ]
        free_space = site.difference(unary_union(buffered))
        if free_space.is_empty or not free_space.is_valid:
            raise ConnectivityGeometryError("explicit obstacle buffers leave no valid free space")
        return site, blocks, free_space

    def _find_gateway(
        self,
        gate_id: str,
        gate: Mapping[str, Any],
        block_shape: Any,
        free_space: Any,
        solver: Mapping[str, Any],
    ) -> GatewaySearchResult:
        gate_point = _point_tuple(gate["position"])
        point = Point(gate_point)
        if block_shape.boundary.distance(point) > self.config.tolerance.linear_m:
            raise ConnectivityGeometryError(f"{gate_id}: Gate must lie on its owning Block boundary")
        centroid = (float(block_shape.centroid.x), float(block_shape.centroid.y))
        direction = (gate_point[0] - centroid[0], gate_point[1] - centroid[1])
        norm = math.hypot(direction[0], direction[1])
        if norm <= self.config.tolerance.linear_m:
            raise ConnectivityGeometryError(f"{gate_id}: cannot derive an outward probe direction")
        preferred = direction[0] / norm, direction[1] / norm
        probes = 0
        searched = 0.0
        interior = block_shape.buffer(-self.config.tolerance.linear_m)
        for unit in _candidate_directions(preferred):
            for distance_m in _probe_distances(
                float(solver["initial_probe_distance_m"]),
                float(solver["gateway_search_step_m"]),
                float(solver["max_gateway_distance_m"]),
                self.config.tolerance.linear_m,
            ):
                probes += 1
                searched = max(searched, distance_m)
                candidate = gate_point[0] + unit[0] * distance_m, gate_point[1] + unit[1] * distance_m
                segment = LineString([gate_point, candidate])
                if not interior.is_empty and interior.intersects(segment):
                    continue
                if free_space.contains(Point(candidate)):
                    return GatewaySearchResult(gate_id, "FOUND", candidate, unit, round(distance_m, 12), probes)
        return GatewaySearchResult(gate_id, "NOT_FOUND", None, None, round(searched, 12), probes)

    def _evaluate_connection(
        self,
        connection: Mapping[str, Any],
        source: GatewaySearchResult,
        target: GatewaySearchResult,
        free_space: Any,
        bounds: tuple[float, float, float, float],
        solver: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], Mapping[str, Any] | None, list[Mapping[str, Any]], PathSearchResult | None]:
        connection_id = str(connection["connection_id"])
        base = {
            "connection_id": connection_id,
            "required": bool(connection["required"]),
            "source_block": str(connection["source_block"]),
            "source_gate": str(connection["source_gate"]),
            "target_block": str(connection["target_block"]),
            "target_gate": str(connection["target_gate"]),
        }
        missing = [value.gate_id for value in (source, target) if value.status != "FOUND"]
        if missing:
            result = {**base, "status": "NO_FEASIBLE_GATEWAY", "path": None}
            failure = {
                "connection_id": connection_id,
                "reason": "NO_FEASIBLE_GATEWAY",
                "affected_gate_ids": missing,
                "affected_block_ids": [base["source_block"], base["target_block"]],
                "evidence": {"gateway_search_status": {source.gate_id: source.status, target.gate_id: target.status}},
                "hint": "Review the externally supplied Gate, Buffer, or search-distance inputs.",
            }
            return result, failure, [], None

        assert source.point is not None and target.point is not None
        path = self._grid_a_star(source.point, target.point, free_space, bounds, solver)
        path_evidence = [{
            "evidence_type": "PATH_SEARCH",
            "connection_id": connection_id,
            "status": path.status,
            "grid_expansions": path.grid_expansions,
            "grid_cells": path.grid_cells,
        }]
        if path.status == "CONNECTED":
            result = {
                **base,
                "status": "CONNECTED",
                "path": {
                    "strategy": PATH_STRATEGY,
                    "coordinates": [_json_point(item) for item in path.coordinates],
                    "length_m": round(float(path.length_m or 0.0), 12),
                },
            }
            return result, None, path_evidence, path

        reason = path.status
        result = {**base, "status": reason, "path": None}
        failure = {
            "connection_id": connection_id,
            "reason": reason,
            "affected_gate_ids": [source.gate_id, target.gate_id],
            "affected_block_ids": [base["source_block"], base["target_block"]],
            "evidence": {"grid_expansions": path.grid_expansions, "grid_cells": path.grid_cells},
            "hint": "An external caller may revise the Problem or invoke SC-TSE again; Core makes no workflow decision.",
        }
        return result, failure, path_evidence, path

    def _grid_a_star(
        self,
        start: tuple[float, float],
        goal: tuple[float, float],
        free_space: Any,
        bounds: tuple[float, float, float, float],
        solver: Mapping[str, Any],
    ) -> PathSearchResult:
        resolution = float(solver["grid_resolution_m"])
        min_x, min_y, max_x, max_y = bounds
        nx = max(1, int(math.ceil((max_x - min_x) / resolution)))
        ny = max(1, int(math.ceil((max_y - min_y) / resolution)))
        grid_cells = nx * ny
        if grid_cells > self.config.max_grid_cells_limit:
            raise ConnectivityGeometryError(
                f"grid contains {grid_cells} cells and exceeds configured max_grid_cells_limit"
            )

        def coordinate(node: tuple[int, int]) -> tuple[float, float]:
            return min_x + (node[0] + 0.5) * resolution, min_y + (node[1] + 0.5) * resolution

        free_nodes = {
            (x_index, y_index)
            for x_index in range(nx)
            for y_index in range(ny)
            if free_space.contains(Point(coordinate((x_index, y_index))))
        }
        start_node = self._nearest_visible_node(start, free_nodes, coordinate, free_space)
        goal_node = self._nearest_visible_node(goal, free_nodes, coordinate, free_space)
        if start_node is None or goal_node is None:
            return PathSearchResult("NO_FEASIBLE_PATH", (), None, 0, grid_cells, "GRID_GRAPH_EXHAUSTION")

        frontier: list[tuple[float, float, int, int, tuple[int, int]]] = []
        start_h = abs(start_node[0] - goal_node[0]) + abs(start_node[1] - goal_node[1])
        heapq.heappush(frontier, (float(start_h), 0.0, start_node[0], start_node[1], start_node))
        came_from: dict[tuple[int, int], tuple[int, int] | None] = {start_node: None}
        cost = {start_node: 0.0}
        expansions = 0
        maximum = int(solver["max_grid_expansions"])

        while frontier:
            _, current_cost, _, _, current = heapq.heappop(frontier)
            if current_cost != cost.get(current):
                continue
            if expansions >= maximum:
                return PathSearchResult("SEARCH_BUDGET_EXHAUSTED", (), None, expansions, grid_cells, "NONE")
            expansions += 1
            if current == goal_node:
                nodes: list[tuple[int, int]] = []
                cursor: tuple[int, int] | None = current
                while cursor is not None:
                    nodes.append(cursor)
                    cursor = came_from[cursor]
                nodes.reverse()
                points = [start] + [coordinate(node) for node in nodes] + [goal]
                deduplicated = tuple(item for index, item in enumerate(points) if index == 0 or item != points[index - 1])
                return PathSearchResult(
                    "CONNECTED",
                    deduplicated,
                    _polyline_length(deduplicated),
                    expansions,
                    grid_cells,
                    "NONE",
                )
            for dx, dy in NEIGHBOR_OFFSETS:
                neighbor = current[0] + dx, current[1] + dy
                if neighbor not in free_nodes:
                    continue
                if not free_space.covers(LineString([coordinate(current), coordinate(neighbor)])):
                    continue
                next_cost = current_cost + 1.0
                if next_cost >= cost.get(neighbor, math.inf):
                    continue
                cost[neighbor] = next_cost
                came_from[neighbor] = current
                heuristic = abs(neighbor[0] - goal_node[0]) + abs(neighbor[1] - goal_node[1])
                heapq.heappush(
                    frontier,
                    (next_cost + heuristic, next_cost, neighbor[0], neighbor[1], neighbor),
                )
        return PathSearchResult("NO_FEASIBLE_PATH", (), None, expansions, grid_cells, "GRID_GRAPH_EXHAUSTION")

    @staticmethod
    def _nearest_visible_node(
        endpoint: tuple[float, float],
        free_nodes: set[tuple[int, int]],
        coordinate: Any,
        free_space: Any,
    ) -> tuple[int, int] | None:
        ranked = sorted(free_nodes, key=lambda node: (_distance(endpoint, coordinate(node)), node[0], node[1]))
        for node in ranked:
            segment = LineString([endpoint, coordinate(node)])
            if free_space.covers(segment):
                return node
        return None


# ============================================================
# Schema / 契约辅助函数
# ============================================================

def evaluate_connectivity(problem: ConnectivityProblem, config: Phase2Config) -> ConnectivityOutcome:
    """Evaluate basic connectivity without changing the supplied problem."""
    return ConnectivityEvaluator(config).evaluate(problem)


# ============================================================
# CLI / main 接口区
# ============================================================

# Import-only computational engine; public CLI is run_sc_tse_phase2_v0001.py.
