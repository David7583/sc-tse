# ============================================================
# 文件名: sc_tse_basic_layout_solver_v0001.py
# 中文名: SC-TSE Phase 1 基础布局求解脚本
# 版本号: v0001
#
# 主层级: action
# 层级: development / sc_tse / phase1_solver_strategy
# 脚本定位: 有限网格候选域上的确定性二维基础布局 Strategy
#
# 职责说明:
# - 在显式 FIXED/FREE 位置域内搜索满足 HARD Constraint 的布局
# - 严格区分 FEASIBLE、有限域 INFEASIBLE 与预算内 UNRESOLVED
#
# 本脚本做什么:
# - 保持 FIXED 体块不动，对 FREE Rectangle 执行确定性回溯网格扫描
# - 返回 LayoutCandidate、约束状态、评分、终止原因和证明能力
#
# 本脚本不做什么:
# - 不决定锁定或释放哪个对象，不修改输入 Problem 或项目状态
# - 不执行 Gate、连接、拓扑、路径、道路、3D 或跨 Solver 调度
#
# 制度边界声明:
# - 仅穷尽显式有限候选域时返回 INFEASIBLE
# - 达到候选预算但未得结论时必须返回 UNRESOLVED
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: sc_tse_basic_layout_solver_v0001
# family: sc_tse_basic_layout_solver
# role: phase1_deterministic_grid_layout_strategy
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/sc_tse/sc_tse_basic_layout_solver_v0001.py
# input:
#   - validated LayoutProblem
#   - Phase1Config
# output:
#   - LayoutSolveOutcome with optional LayoutCandidate
# depends_on:
#   - Python stdlib: copy, dataclasses, hashlib, json, math, pathlib, sys, typing
#   - sc_tse_geometry_v0001
#   - sc_tse_layout_contracts_v0001
#   - sc_tse_constraint_evaluator_v0001
# used_by:
#   - run_sc_tse_phase1_v0001
#   - test_sc_tse_phase1_v0001
# ============================================================

from __future__ import annotations

import copy
import hashlib
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from sc_tse_constraint_evaluator_v0001 import (
    ConstraintEvaluator,
    hard_violations,
    soft_constraint_score,
)
from sc_tse_layout_contracts_v0001 import LayoutProblem, Phase1Config


DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "sc_tse_basic_layout_solver"
SCRIPT_NAME = "sc_tse_basic_layout_solver_v0001"
SCRIPT_VERSION = "v0001"


# ============================================================
# Phase 0 几何校验导入
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
    validate_problem_geometries,
)


# ============================================================
# 异常类型
# ============================================================

class ScTseLayoutSolverError(RuntimeError):
    """Base error for Phase 1 layout strategy failures."""


# ============================================================
# 数据结构
# ============================================================

@dataclass(frozen=True)
class LayoutSolveOutcome:
    execution_status: str
    solve_status: str
    solution_quality: str
    layout_candidate: Mapping[str, Any] | None
    termination_reason: str
    candidate_evaluations: int
    search_exhausted: bool
    proof_capability: str
    diagnostics: Mapping[str, Any]
    evidence: tuple[Mapping[str, Any], ...]


# ============================================================
# 工具函数区
# ============================================================

def _stable_layout_id(problem_id: str, blocks: Sequence[Mapping[str, Any]]) -> str:
    canonical = json.dumps(blocks, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(canonical.encode(DEFAULT_ENCODING)).hexdigest()[:16]
    return f"{problem_id}-layout-{digest}"


def _quality_from_score(score: float) -> str:
    if score >= 1.0:
        return "GOOD"
    if score > 0.0:
        return "ACCEPTABLE"
    return "POOR"


def _candidate_centers(
    initial_center: Sequence[Any],
    bounds: tuple[float, float, float, float],
    grid_step_m: float,
    linear_tolerance_m: float,
) -> Iterator[tuple[float, float]]:
    initial = (float(initial_center[0]), float(initial_center[1]))
    yielded = {(round(initial[0], 12), round(initial[1], 12))}
    yield initial

    min_x, min_y, max_x, max_y = bounds
    x_count = max(1, int(math.floor((max_x - min_x + linear_tolerance_m) / grid_step_m)) + 1)
    y_count = max(1, int(math.floor((max_y - min_y + linear_tolerance_m) / grid_step_m)) + 1)
    for y_index in range(y_count):
        y_value = min_y + y_index * grid_step_m
        for x_index in range(x_count):
            x_value = min_x + x_index * grid_step_m
            key = (round(x_value, 12), round(y_value, 12))
            if key in yielded:
                continue
            yielded.add(key)
            yield float(x_value), float(y_value)


def _block_sequence(
    original_blocks: Sequence[Mapping[str, Any]],
    block_by_id: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    return [copy.deepcopy(dict(block_by_id[str(block["block_id"])])) for block in original_blocks]


# ============================================================
# 默认映射
# ============================================================

STRATEGY_NAME = "DETERMINISTIC_GRID_SCAN"
PROOF_CAPABILITY = "FINITE_CANDIDATE_ENUMERATION"


# ============================================================
# 核心类
# ============================================================

class DeterministicGridLayoutSolver:
    """Search the explicit initial-plus-grid finite placement domain."""

    def __init__(self, config: Phase1Config) -> None:
        self.config = config
        self.evaluator = ConstraintEvaluator(config)

    def solve(self, problem: LayoutProblem) -> LayoutSolveOutcome:
        payload = problem.payload
        validate_problem_geometries(payload, self.config.tolerance)
        site = payload["site"]
        constraints = tuple(payload["constraints"])
        original_blocks = tuple(payload["blocks"])
        block_by_id = {str(item["block_id"]): copy.deepcopy(dict(item)) for item in original_blocks}
        fixed = {
            object_id: block_by_id[object_id]
            for object_id, mode in problem.position_modes.items()
            if mode == "FIXED"
        }
        free_ids = tuple(
            str(item["block_id"])
            for item in original_blocks
            if problem.position_modes[str(item["block_id"])] == "FREE"
        )

        fixed_statuses = self.evaluator.evaluate_available(site, fixed, constraints)
        fixed_violations = hard_violations(fixed_statuses)
        if fixed_violations:
            return LayoutSolveOutcome(
                execution_status="COMPLETED",
                solve_status="INFEASIBLE",
                solution_quality="N/A",
                layout_candidate=None,
                termination_reason="LOCK_CONFLICT_WITH_HARD_CONSTRAINT",
                candidate_evaluations=0,
                search_exhausted=True,
                proof_capability=PROOF_CAPABILITY,
                diagnostics={
                    "detail": "FIXED positions violate a HARD constraint and cannot be changed.",
                    "violated_constraint_ids": [item.constraint_id for item in fixed_violations],
                },
                evidence=tuple(item.to_dict() for item in fixed_violations),
            )

        solver_config = payload["solver_config"]
        grid_step_m = float(solver_config["grid_step_m"])
        max_evaluations = int(solver_config["max_candidate_evaluations"])
        site_shape = self.evaluator.geometry.geometry(site["geometry"], str(site["site_id"]))
        site_bounds = tuple(float(value) for value in site_shape.bounds)
        placed: dict[str, Mapping[str, Any]] = dict(fixed)
        evaluations = 0
        budget_exhausted = False
        solved_blocks: dict[str, Mapping[str, Any]] | None = None

        def search(free_index: int) -> bool:
            nonlocal evaluations, budget_exhausted, solved_blocks
            if free_index >= len(free_ids):
                statuses = self.evaluator.evaluate_available(site, placed, constraints)
                if hard_violations(statuses):
                    return False
                solved_blocks = copy.deepcopy(placed)
                return True

            object_id = free_ids[free_index]
            source_block = block_by_id[object_id]
            geometry = source_block["geometry"]
            for center in _candidate_centers(
                geometry["center"],
                site_bounds,
                grid_step_m,
                self.config.tolerance.linear_m,
            ):
                if evaluations >= max_evaluations:
                    budget_exhausted = True
                    return False
                evaluations += 1
                candidate = copy.deepcopy(source_block)
                candidate["geometry"]["center"] = [center[0], center[1]]
                placed[object_id] = candidate
                statuses = self.evaluator.evaluate_available(site, placed, constraints)
                if not hard_violations(statuses) and search(free_index + 1):
                    return True
                placed.pop(object_id, None)
                if budget_exhausted:
                    return False
            return False

        found = search(0)
        if found and solved_blocks is not None:
            final_statuses = self.evaluator.evaluate_available(site, solved_blocks, constraints)
            score = soft_constraint_score(final_statuses)
            blocks = _block_sequence(original_blocks, solved_blocks)
            candidate = {
                "layout_id": _stable_layout_id(problem.problem_id, blocks),
                "blocks": blocks,
                "hard_constraint_status": [
                    item.to_dict() for item in final_statuses if item.hardness == "HARD"
                ],
                "soft_constraint_score": score,
            }
            return LayoutSolveOutcome(
                execution_status="COMPLETED",
                solve_status="FEASIBLE",
                solution_quality=_quality_from_score(score),
                layout_candidate=candidate,
                termination_reason="FEASIBLE_LAYOUT_FOUND",
                candidate_evaluations=evaluations,
                search_exhausted=False,
                proof_capability=PROOF_CAPABILITY,
                diagnostics={
                    "detail": "A layout satisfying all evaluated HARD constraints was found.",
                    "fixed_block_ids": sorted(fixed),
                    "free_block_ids": list(free_ids),
                },
                evidence=tuple(item.to_dict() for item in final_statuses),
            )

        if budget_exhausted:
            return LayoutSolveOutcome(
                execution_status="INTERRUPTED",
                solve_status="UNRESOLVED",
                solution_quality="UNKNOWN",
                layout_candidate=None,
                termination_reason="CANDIDATE_BUDGET_EXHAUSTED",
                candidate_evaluations=evaluations,
                search_exhausted=False,
                proof_capability="NONE",
                diagnostics={
                    "detail": "The configured candidate budget ended before the finite domain was exhausted.",
                    "max_candidate_evaluations": max_evaluations,
                },
                evidence=(),
            )

        return LayoutSolveOutcome(
            execution_status="COMPLETED",
            solve_status="INFEASIBLE",
            solution_quality="N/A",
            layout_candidate=None,
            termination_reason="FINITE_CANDIDATE_DOMAIN_EXHAUSTED",
            candidate_evaluations=evaluations,
            search_exhausted=True,
            proof_capability=PROOF_CAPABILITY,
            diagnostics={
                "detail": "No feasible layout exists in the explicit initial-plus-grid candidate domain.",
                "grid_step_m": grid_step_m,
            },
            evidence=(
                {
                    "evidence_type": "FINITE_DOMAIN_EXHAUSTION",
                    "placement_domain": solver_config["placement_domain"],
                    "candidate_evaluations": evaluations,
                },
            ),
        )


# ============================================================
# Schema / 契约辅助函数
# ============================================================

def solve_layout(problem: LayoutProblem, config: Phase1Config) -> LayoutSolveOutcome:
    return DeterministicGridLayoutSolver(config).solve(problem)


# ============================================================
# CLI / main 接口区
# ============================================================

# This strategy is import-only; the public CLI is run_sc_tse_phase1_v0001.py.
