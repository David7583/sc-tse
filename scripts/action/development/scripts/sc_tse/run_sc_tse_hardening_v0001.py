# ============================================================
# 文件名: run_sc_tse_hardening_v0001.py
# 中文名: SC-TSE Baseline Hardening 独立入口
# 版本号: v0001
#
# 主层级: action
# 层级: development / sc_tse / baseline_hardening
# 脚本定位: baseline_hardening_entry
#
# 职责说明:
# - 调用现有契约下的一次计算并提供独立 telemetry sidecar
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
# alias: run_sc_tse_hardening_v0001
# family: run_sc_tse_hardening
# role: baseline_hardening_entry
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/sc_tse/run_sc_tse_hardening_v0001.py
# input:
#   - validated computational input and configuration
# output:
#   - unchanged Core Result and optional metrics JSON
# depends_on:
#   - sc_tse_hardening_compute_v0001
#   - run_sc_tse_phase0_v0001
#   - run_sc_tse_phase1_v0001
#   - run_sc_tse_phase2_v0001
#   - run_sc_tse_phase3_v0001
# used_by:
#   - benchmark_sc_tse_hardening_v0001
#   - test_sc_tse_hardening_v0001
# ============================================================

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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

import run_sc_tse_phase0_v0001 as p0
import run_sc_tse_phase1_v0001 as p1
import run_sc_tse_phase2_v0001 as p2
import run_sc_tse_phase3_v0001 as p3
from sc_tse_constraint_evaluator_v0001 import SolverCapabilityError
from sc_tse_hardening_compute_v0001 import (
    Telemetry, ObservedLayoutSolver, ObservedConnectivityEvaluator,
)


DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "run_sc_tse_hardening"
SCRIPT_NAME = "run_sc_tse_hardening_v0001"
SCRIPT_VERSION = "v0001"

PROJECT_ROOT = _bootstrap_root
FORMAL_ROOT = _bootstrap_formal

# ============================================================
# 异常与配置数据结构
# ============================================================

class HardeningConfigError(ValueError):
    pass


@dataclass(frozen=True)
class HardeningOptions:
    reuse_grid: bool = True
    incremental_constraints: bool = False


# ============================================================
# 工具函数区
# ============================================================

def load_options(path):
    try:
        data = json.loads(Path(path).read_text(encoding=DEFAULT_ENCODING))
    except (OSError, ValueError) as exc:
        raise HardeningConfigError(str(exc)) from exc
    if not isinstance(data, dict) or set(data) != {"config_version", "reuse_grid", "incremental_constraints"}:
        raise HardeningConfigError("unexpected or missing hardening config fields")
    if data["config_version"] != "sc-tse-baseline-hardening-v0.1.0":
        raise HardeningConfigError("hardening config version mismatch")
    if any(type(data[k]) is not bool for k in ("reuse_grid", "incremental_constraints")):
        raise HardeningConfigError("hardening flags must be boolean")
    return HardeningOptions(data["reuse_grid"], data["incremental_constraints"])


def write_new_json(path, value):
    """Atomic no-overwrite evidence publication; no original inputs overwritten."""
    path = Path(path).resolve()
    if path.exists():
        raise FileExistsError(f"refusing to overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".sc_tse_", dir=path.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding=DEFAULT_ENCODING) as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        # Hard-link publication is atomic and fails if destination exists, also on Windows.
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


# ============================================================
# 核心类：仅替换内部计算实例，原有结果组装逐字段保留
# ============================================================

class HardeningGeometryKernel(p0.Phase0Kernel):
    def __init__(self, config_path, metrics):
        self.metrics = metrics
        with metrics.stage("config_and_schema_setup"):
            super().__init__(config_path)

    def evaluate(self, problem_path: str | Path, *, dry_run: bool = False) -> dict[str, Any]:
        with self.metrics.stage("input_validation"):
            problem = self.loader.load(problem_path)
        with self.metrics.stage("geometry_validation"):
            validations = p0.validate_problem_geometries(problem.payload, self.config.tolerance)
        with self.metrics.stage("geometry_relations"):
            relations = p0.evaluate_block_relations(problem.payload, self.config.tolerance)
        result = {
            "schema_version": self.config.result_schema_version,
            "mode": "DRY_RUN" if dry_run else "RUN",
            "problem_id": problem.problem_id,
            "execution_status": "COMPLETED",
            "solve_status": "NOT_EVALUATED",
            "solution_quality": "N/A",
            "geometry_validations": [item.to_dict() for item in validations],
            "geometry_relations": [item.to_dict() for item in relations],
            "diagnostics": {
                "validated_geometry_count": len(validations),
                "evaluated_relation_count": len(relations),
                "detail": "Phase 0 validates geometry facts; no layout solve was executed.",
            },
            "evidence": [
                {
                    "evidence_type": "CONTRACT_VERSION",
                    "value": self.config.problem_schema_version,
                },
                {
                    "evidence_type": "NUMERICAL_TOLERANCE",
                    "value": {
                        "linear_m": self.config.tolerance.linear_m,
                        "area_m2": self.config.tolerance.area_m2,
                        "z_m": self.config.tolerance.z_m,
                    },
                },
            ],
            "solver_metadata": {
                "strategy": None,
                "proof_capability": "NONE",
                "phase": "PHASE_0",
            },
        }
        with self.metrics.stage("result_validation"):
            p0._validate_result(result, self.result_schema_path)
        return result


class HardeningLayoutKernel(p1.Phase1LayoutKernel):
    def __init__(self, config_path, metrics, options):
        self.metrics, self.options = metrics, options
        with metrics.stage("config_and_schema_setup"):
            super().__init__(config_path)

    def evaluate(self, problem_path: str | Path, *, dry_run: bool = False) -> dict[str, Any]:
        with self.metrics.stage("input_validation"):
            problem = self.loader.load(problem_path)
        try:
            if dry_run:
                return self._dry_run(problem)
            with self.metrics.stage("layout_total"):
                outcome = ObservedLayoutSolver(self.config, self.metrics, self.options.incremental_constraints).solve(problem)
            result = {
                "schema_version": self.config.result_schema_version,
                "mode": "RUN",
                "problem_id": problem.problem_id,
                "execution_status": outcome.execution_status,
                "solve_status": outcome.solve_status,
                "solution_quality": outcome.solution_quality,
                "layout_candidate": outcome.layout_candidate,
                "diagnostics": dict(outcome.diagnostics),
                "evidence": [dict(item) for item in outcome.evidence],
                "solver_metadata": {
                    "phase": "PHASE_1",
                    "strategy": "DETERMINISTIC_GRID_SCAN",
                    "proof_capability": outcome.proof_capability,
                    "termination_reason": outcome.termination_reason,
                    "candidate_evaluations": outcome.candidate_evaluations,
                    "search_exhausted": outcome.search_exhausted,
                },
            }
        except SolverCapabilityError as exc:
            result = p1._capability_result(problem.problem_id, exc)
        with self.metrics.stage("result_validation"):
            p1._validate_result(result, self.result_schema_path)
        return result


class HardeningConnectivityKernel(p2.Phase2ConnectivityKernel):
    def __init__(self, config_path, metrics, options):
        self.metrics, self.options = metrics, options
        with metrics.stage("config_and_schema_setup"):
            super().__init__(config_path)

    def evaluate(self, problem_path: str | Path, *, dry_run: bool = False) -> dict[str, Any]:
        with self.metrics.stage("input_validation"):
            problem = self.loader.load(problem_path)
        if dry_run:
            result = self._dry_run(problem)
        else:
            outcome = ObservedConnectivityEvaluator(self.config, self.metrics, self.options.reuse_grid).evaluate(problem)
            result = {
                "schema_version": self.config.result_schema_version,
                "mode": "RUN",
                "problem_id": problem.problem_id,
                "execution_status": outcome.execution_status,
                "solve_status": outcome.solve_status,
                "solution_quality": outcome.solution_quality,
                "connectivity_status": outcome.connectivity_status,
                "connection_results": [dict(item) for item in outcome.connection_results],
                "topology": dict(outcome.topology),
                "failure_data": [dict(item) for item in outcome.failure_data],
                "diagnostics": dict(outcome.diagnostics),
                "evidence": [dict(item) for item in outcome.evidence],
                "solver_metadata": {
                    "phase": "PHASE_2",
                    "path_strategy": "GRID_A_STAR_4_NEIGHBOR",
                    "proof_capability": outcome.proof_capability,
                    "termination_reason": outcome.termination_reason,
                    "grid_expansions": outcome.grid_expansions,
                },
            }
        with self.metrics.stage("result_validation"):
            p2._validate_result(result, self.result_schema_path)
        return result


class HardeningFeedbackKernel(p3.Phase3FeedbackKernel):
    def __init__(self, config_path, metrics, options):
        self.metrics, self.options = metrics, options
        with metrics.stage("config_and_schema_setup"):
            super().__init__(config_path)

    def evaluate(
        self,
        problem_class: str,
        problem_path: str | Path,
        *,
        previous_feedback_path: str | Path | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        if problem_class not in self.config.supported_problem_classes:
            raise p3.ScTsePhase3Error(f"unsupported problem_class: {problem_class}")
        previous = p3.load_previous_feedback(
            previous_feedback_path,
            self.config,
            self.feedback_schema_path,
        )
        source_config = p3._resolve_project_path(
            self.project_root,
            self.config.source_solver_configs[problem_class],
        )
        if problem_class == "LAYOUT":
            source_result = HardeningLayoutKernel(source_config, self.metrics, self.options).evaluate(problem_path, dry_run=dry_run)
        else:
            source_result = HardeningConnectivityKernel(source_config, self.metrics, self.options).evaluate(problem_path, dry_run=dry_run)
        with self.metrics.stage("feedback_generation"):
            generated = p3.generate_layout_feedback(problem_class, source_result, self.config)
        with self.metrics.stage("feedback_validation"):
            p3._validate_mapping(generated.feedback_batch, self.feedback_schema_path, "feedback batch")
        result = {
            "schema_version": self.config.result_schema_version,
            "mode": "DRY_RUN" if dry_run else "RUN",
            "problem_class": problem_class,
            "problem_id": source_result["problem_id"],
            "execution_status": source_result["execution_status"],
            "solve_status": source_result["solve_status"],
            "solution_quality": source_result["solution_quality"],
            "source_result_schema_version": source_result["schema_version"],
            "source_result_sha256": p3.canonical_sha256(source_result),
            "computational_result": source_result,
            "feedback_batch": dict(generated.feedback_batch),
            "feedback_context": p3._feedback_context(previous),
            "diagnostics": {
                "detail": "One caller-selected computation completed; feedback was generated without orchestration.",
                "generated_feedback_count": generated.feedback_count,
                "previous_feedback_source_problem_id": previous.source_problem_id if previous is not None else None,
                "feedback_generation_only": True,
            },
            "evidence": [dict(item) for item in generated.evidence],
            "solver_metadata": {
                "phase": "PHASE_3",
                "strategy": "STRUCTURED_FEEDBACK_FROM_COMPUTATION",
                "source_solver_invocations": self.config.max_source_solver_invocations_per_call,
                "feedback_cycles": self.config.max_feedback_cycles,
                "automatic_problem_mutations": self.config.max_automatic_problem_mutations,
                "termination_reason": "DRY_RUN_PRECHECK_COMPLETED" if dry_run else "SINGLE_COMPUTATION_COMPLETED",
            },
        }
        with self.metrics.stage("phase3_result_validation"):
            p3._validate_mapping(result, self.result_schema_path, "Phase 3 result")
        return result

# ============================================================
# 契约兼容入口
# ============================================================

def run_hardening(phase, config_path, problem_path, *, metrics=None, options=None,
                  problem_class="CONNECTIVITY", previous_feedback_path=None, dry_run=False):
    metrics = metrics if metrics is not None else Telemetry()
    options = options if options is not None else HardeningOptions()
    with metrics.stage("total"):
        if phase == 0:
            return HardeningGeometryKernel(config_path, metrics).evaluate(problem_path, dry_run=dry_run)
        if phase == 1:
            return HardeningLayoutKernel(config_path, metrics, options).evaluate(problem_path, dry_run=dry_run)
        if phase == 2:
            return HardeningConnectivityKernel(config_path, metrics, options).evaluate(problem_path, dry_run=dry_run)
        if phase == 3:
            return HardeningFeedbackKernel(config_path, metrics, options).evaluate(
                problem_class, problem_path, previous_feedback_path=previous_feedback_path, dry_run=dry_run)
        raise HardeningConfigError("phase must be 0, 1, 2 or 3")


# ============================================================
# CLI / main 接口区
# ============================================================

def _build_parser():
    parser = argparse.ArgumentParser(description="Opt-in Phase 0-3 Baseline Hardening; not Phase 4.")
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config/action/config/sc_tse_hardening_config_v0001.json"))
    parser.add_argument("--source-config")
    parser.add_argument("--phase", required=True, type=int, choices=[0, 1, 2, 3])
    parser.add_argument("--problem", required=True)
    parser.add_argument("--problem-class", choices=["LAYOUT", "CONNECTIVITY"], default="CONNECTIVITY")
    parser.add_argument("--previous-feedback")
    parser.add_argument("--metrics-output")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding=DEFAULT_ENCODING)
    args = _build_parser().parse_args()
    metrics = Telemetry()
    result = None
    publish_metrics = False
    formatter = {0: p0._error_result, 1: p1._error_result, 2: p2._error_result, 3: p3._error_result}[args.phase]
    try:
        options = load_options(args.config)
        if args.metrics_output and not args.dry_run and Path(args.metrics_output).exists():
            raise HardeningConfigError("metrics-output already exists; refusing overwrite")
        publish_metrics = bool(args.metrics_output) and not args.dry_run
        config = args.source_config or PROJECT_ROOT / f"config/action/config/sc_tse_phase{args.phase}_config_v0001.json"
        result = run_hardening(args.phase, config, args.problem, metrics=metrics, options=options,
                               problem_class=args.problem_class, previous_feedback_path=args.previous_feedback,
                               dry_run=args.dry_run)
        code = 4 if result["execution_status"] == "INTERRUPTED" else 0
    except Exception as exc:
        result = formatter(exc, args.problem_class) if args.phase == 3 else formatter(exc)
        code = 2 if isinstance(exc, (HardeningConfigError,) + p0.CLASSIFIED_ERRORS + p3.CLASSIFIED_INPUT_ERRORS) else 3
    if publish_metrics:
        try:
            write_new_json(args.metrics_output, metrics.to_dict())
        except Exception as exc:
            result = formatter(exc, args.problem_class) if args.phase == 3 else formatter(exc)
            code = 3
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
