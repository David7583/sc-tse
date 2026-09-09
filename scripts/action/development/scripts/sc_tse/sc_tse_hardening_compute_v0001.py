# ============================================================
# 文件名: sc_tse_hardening_compute_v0001.py
# 中文名: SC-TSE 请求内计算加固
# 版本号: v0001
#
# 主层级: action
# 层级: development / sc_tse / baseline_hardening
# 脚本定位: request_scoped_computation_hardening
#
# 职责说明:
# - 执行请求内网格复用、阶段观测、预算估算与增量约束实验
#
# 本脚本做什么:
# - 保留 Baseline，执行可观测、可验证的局部加固
#
# 本脚本不做什么:
# - 不改变 Core Contract、求解语义或 Phase 4，不执行业务编排
#
# 制度边界声明:
# - 输入只读；仅显式指定的测试或观测目录允许产生新文件，拒绝覆盖
# - 失败显式报告；请求内状态可靠释放，不写业务数据库或密钥
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: sc_tse_hardening_compute_v0001
# family: sc_tse_hardening_compute
# role: request_scoped_computation_hardening
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/sc_tse/sc_tse_hardening_compute_v0001.py
# input:
#   - validated computational input and configuration
# output:
#   - baseline-compatible outcome and independent telemetry
# depends_on:
#   - sc_tse_connectivity_engine_v0001
#   - sc_tse_basic_layout_solver_v0001
#   - sc_tse_constraint_evaluator_v0001
#   - Python stdlib and Shapely
# used_by:
#   - run_sc_tse_hardening_v0001
# ============================================================

from __future__ import annotations

import copy
import heapq
import json
import math
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

# Import bootstrap follows the existing Phase 3 runner; no CLI runs on import.
for _parent in Path(__file__).resolve().parents:
    if (_parent / "PROGRAMMING_LANGUAGE_SCRIPT_FORMAT.md").is_file():
        _bootstrap_root = _parent
        break
else:
    raise RuntimeError("cannot locate project root")
_bootstrap_formal = _bootstrap_root / "scripts/action/development/scripts/sc_tse"
if str(_bootstrap_formal) not in sys.path:
    sys.path.append(str(_bootstrap_formal))

from shapely.geometry import Point, LineString
from sc_tse_connectivity_engine_v0001 import (
    ConnectivityEvaluator, ConnectivityGeometryError, PathSearchResult,
    NEIGHBOR_OFFSETS, _polyline_length,
)
from sc_tse_basic_layout_solver_v0001 import DeterministicGridLayoutSolver
from sc_tse_constraint_evaluator_v0001 import ConstraintEvaluator, _block_subjects


DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "sc_tse_hardening_compute"
SCRIPT_NAME = "sc_tse_hardening_compute_v0001"
SCRIPT_VERSION = "v0001"

PROJECT_ROOT = _bootstrap_root
FORMAL_ROOT = _bootstrap_formal

# ============================================================
# 数据结构
# ============================================================

@dataclass
class Telemetry:
    """Sidecar only: inclusive stage times, never part of Core Result."""
    stage_ms: dict[str, float] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)
    preflight: dict[str, Any] = field(default_factory=dict)

    @contextmanager
    def stage(self, name):
        started = time.perf_counter()
        try:
            yield
        finally:
            self.stage_ms[name] = self.stage_ms.get(name, 0.0) + (time.perf_counter() - started) * 1000

    def count(self, name, amount=1):
        self.counters[name] = self.counters.get(name, 0) + amount

    def to_dict(self):
        stages = dict(self.stage_ms)
        if "path_total" in stages:
            stages["search_and_path_assembly_residual"] = max(
                0.0, stages["path_total"] - stages.get("grid_build", 0.0) - stages.get("endpoint_attachment", 0.0))
        return {"telemetry_version": "sc-tse-hardening-telemetry-v0.1.0",
                "timing_semantics": "inclusive; nested stages must not be summed",
                "stage_ms": stages, "counters": dict(self.counters),
                "preflight": copy.deepcopy(self.preflight)}


# ============================================================
# 核心类：请求内约束评估实验
# ============================================================

class ObservedConstraints(ConstraintEvaluator):
    """Last value per constraint only; exact operands, no unbounded candidate cache."""

    def __init__(self, config, metrics, incremental=False):
        super().__init__(config)
        self.metrics = metrics
        self.incremental = incremental
        self.cache = {}

    def evaluate_available(self, *args, **kwargs):
        self.metrics.count("constraint_evaluation_calls")
        with self.metrics.stage("constraint_evaluation"):
            results = super().evaluate_available(*args, **kwargs)
        self.metrics.count("constraint_status_records", len(results))
        return results

    def _evaluate_one(self, site, blocks, constraint):
        self.metrics.count("constraint_requests")
        if not self.incremental:
            self.metrics.count("constraint_computations")
            return super()._evaluate_one(site, blocks, constraint)
        # Full constraint/site and subject geometries are all current evaluator operands.
        # Exact JSON numbers, no coordinate rounding; result copies prevent consumer mutation.
        signature = json.dumps(
            [site, constraint, [(s, blocks[s]["geometry"]) for s in _block_subjects(constraint)]],
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        )
        key = str(constraint["constraint_id"])
        previous = self.cache.get(key)
        if previous is not None and previous[0] == signature:
            self.metrics.count("constraint_cache_hits")
            return copy.deepcopy(previous[1])
        self.metrics.count("constraint_computations")
        result = super()._evaluate_one(site, blocks, constraint)
        self.cache[key] = (signature, copy.deepcopy(result))
        self.metrics.counters["constraint_cache_entries_peak"] = max(
            len(self.cache), self.metrics.counters.get("constraint_cache_entries_peak", 0))
        return result


class ObservedLayoutSolver(DeterministicGridLayoutSolver):
    def __init__(self, config, metrics, incremental=False):
        super().__init__(config)
        self.metrics = metrics
        self.evaluator = ObservedConstraints(config, metrics, incremental)

    def solve(self, problem):
        # Advisory only: no decisions, no enumeration and no altered validation order.
        with self.metrics.stage("budget_preflight"):
            try:
                p = problem.payload
                bounds = self.evaluator.geometry.geometry(p["site"]["geometry"], p["site"]["site_id"]).bounds
                step = float(p["solver_config"]["grid_step_m"])
                nx = max(1, math.floor((bounds[2] - bounds[0] + self.config.tolerance.linear_m) / step) + 1)
                ny = max(1, math.floor((bounds[3] - bounds[1] + self.config.tolerance.linear_m) / step) + 1)
                per_block = 1 + nx * ny
                free = sum(mode == "FREE" for mode in problem.position_modes.values())
                self.metrics.preflight.update({
                    "advisory_only": True, "candidate_centers_per_free_block_upper_bound": per_block,
                    "free_blocks": free, "candidate_leaf_domain_log10_upper_bound": free * math.log10(per_block),
                    "candidate_budget": p["solver_config"]["max_candidate_evaluations"],
                    "limitations": "upper bound includes initial/grid duplicate; ignores constraint pruning; not a time or memory guarantee",
                })
            except Exception as exc:
                self.metrics.preflight["estimate_unavailable"] = type(exc).__name__
        try:
            with self.metrics.stage("layout_solve"):
                outcome = super().solve(problem)
            self.metrics.count("candidate_evaluations", outcome.candidate_evaluations)
            return outcome
        finally:
            self.evaluator.cache.clear()
            self.metrics.counters["constraint_cache_entries_after"] = 0


# ============================================================
# 核心类：请求内网格复用，搜索算法保持 Baseline
# ============================================================

class ObservedConnectivityEvaluator(ConnectivityEvaluator):
    def __init__(self, config, metrics, reuse_grid=True):
        super().__init__(config)
        self.metrics = metrics
        self.reuse_grid = reuse_grid
        self._grid_cache = None

    def evaluate(self, problem):
        self._grid_cache = None
        try:
            with self.metrics.stage("connectivity_evaluate"):
                return super().evaluate(problem)
        finally:
            self._grid_cache = None
            self.metrics.counters["grid_cache_entries_after"] = 0

    def _free_space(self, problem):
        self.metrics.count("free_space_builds")
        with self.metrics.stage("geometry_and_free_space"):
            result = super()._free_space(problem)
        with self.metrics.stage("budget_preflight"):
            try:
                bounds = result[0].bounds
                solver = problem.payload["solver_config"]
                resolution = float(solver["grid_resolution_m"])
                cells = max(1, math.ceil((bounds[2]-bounds[0])/resolution)) * max(1, math.ceil((bounds[3]-bounds[1])/resolution))
                self.metrics.preflight.update({
                    "advisory_only": True, "grid_cells": cells,
                    "grid_cap": self.config.max_grid_cells_limit,
                    "exceeds_existing_grid_cap": cells > self.config.max_grid_cells_limit,
                    "connections": len(problem.payload["connection_requirements"]),
                    "expansion_budget_per_connection": solver["max_grid_expansions"],
                    "limitations": "grid cap is enforced at the original search boundary; cells are not bytes; low expansion budget does not avoid grid construction",
                })
            except Exception as exc:
                self.metrics.preflight["estimate_unavailable"] = type(exc).__name__
        return result

    def _find_gateway(self, *args, **kwargs):
        self.metrics.count("gateway_searches")
        with self.metrics.stage("gateway_search"):
            return super()._find_gateway(*args, **kwargs)

    def _nearest_visible_node(self, *args, **kwargs):
        with self.metrics.stage("endpoint_attachment"):
            return super()._nearest_visible_node(*args, **kwargs)

    def _grid_a_star(self, *args, **kwargs):
        self.metrics.count("path_searches")
        with self.metrics.stage("path_total"):
            result = self._search(*args, **kwargs)
        self.metrics.count("grid_expansions", result.grid_expansions)
        return result

    def _free_nodes(self, free_space, bounds, resolution, nx, ny, coordinate):
        # Keep the actual geometry reference; cache is empty on every evaluate boundary.
        key = (bounds, resolution, nx, ny)
        if self.reuse_grid and self._grid_cache is not None:
            cached_space, cached_key, nodes = self._grid_cache
            if cached_space is free_space and cached_key == key:
                self.metrics.count("grid_cache_hits")
                return nodes
        self.metrics.count("grid_builds")
        self.metrics.count("grid_cells_tested", nx * ny)
        with self.metrics.stage("grid_build"):
            nodes = {(x, y) for x in range(nx) for y in range(ny)
                     if free_space.contains(Point(coordinate((x, y))))}
        if self.reuse_grid:
            self._grid_cache = (free_space, key, nodes)
        return nodes

    def _search(
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

        free_nodes = self._free_nodes(free_space, bounds, resolution, nx, ny, coordinate)
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


# ============================================================
# CLI / main 接口区
# ============================================================

# Import-only computation; public CLI: run_sc_tse_hardening_v0001.py.
