# ============================================================
# 文件名: run_sc_tse_phase4_v0001.py
# 中文名: Phase 4 道路与竖向计算入口
# 版本号: v0001
#
# 主层级: action
# 层级: development / sc_tse / phase4
# 脚本定位: road_engineering_entry
#
# 职责说明:
# - Phase 4 道路与竖向计算入口
#
# 本脚本做什么:
# - 校验配置，调用道路构造和工程校核，原子发布显式输出
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
# alias: run_sc_tse_phase4_v0001
# family: run_sc_tse_phase4
# role: road_engineering_entry
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/sc_tse/run_sc_tse_phase4_v0001.py
# input:
#   - RoadProblem and sc_tse_phase4_config_v0001.json
# output:
#   - RoadRun JSON with candidate and engineering evidence
# depends_on:
#   - sc_tse_road_contracts_v0001
#   - sc_tse_road_alignment_v0001
#   - sc_tse_road_constraints_v0001
#   - sc_tse_road_vertical_v0001
#   - sc_tse_road_metrics_v0001
#   - run_sc_tse_hardening_v0001
#   - jsonschema
# used_by:
#   - test_sc_tse_phase4_v0001
# ============================================================

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import jsonschema

from sc_tse_road_contracts_v0001 import (
    CONTRACT_VERSION, AlignmentUnavailable, RoadError, SearchLimit,
    canonical_hash, make_context, project_root, read_json,
)
from sc_tse_road_alignment_v0001 import edge_setbacks, fillet_alignment, inset_waypoints, sample_alignment
from sc_tse_road_constraints_v0001 import evaluate_road
from sc_tse_road_vertical_v0001 import evaluate_vertical
from sc_tse_road_metrics_v0001 import calculate_road_metrics

# Staging loads these candidate siblings first, then reuses the active atomic writer.
_ACTIVE_DIRECTORY = project_root() / "scripts/action/development/scripts/sc_tse"
if str(_ACTIVE_DIRECTORY) not in sys.path:
    sys.path.append(str(_ACTIVE_DIRECTORY))
from run_sc_tse_hardening_v0001 import write_new_json

DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "run_sc_tse_phase4"
SCRIPT_NAME = "run_sc_tse_phase4_v0001"
SCRIPT_VERSION = "v0001"
DEFAULT_CONFIG_PATH = "config/action/config/sc_tse_phase4_config_v0001.json"


# ============================================================
# 数据结构
# ============================================================

# RoadRun and RoadCandidate are versioned by the companion result JSON Schema.


# ============================================================
# 工具函数区
# ============================================================

def candidate_specs(context):
    p, c = context.problem, context.config
    if "route" in p:
        yield {"strategy": "EXTERNAL_WAYPOINTS", **p["route"]}
        return
    for width in c["width_candidates_m"]:
        for offset in c["search"]["inset_offsets_m"]:
            yield {"strategy": "REDLINE_MITER_RING", "width_m": width, "offset_m": offset,
                   "closed": True, "centerline_radius_m": c["turning"]["centerline_radius_m"]}


# ============================================================
# 核心类
# ============================================================

def run_phase4(problem: dict, config: dict, dry_run: bool = False) -> dict:
    start = time.perf_counter()
    context = make_context(problem, config)
    if dry_run:
        return {"schema_version": CONTRACT_VERSION, "record_type": "RoadDryRun", "status": "dry_run",
                "problem_hash": context.problem_hash, "config_hash": context.config_hash}
    result = {"schema_version": CONTRACT_VERSION, "record_type": "RoadRun",
              "execution_status": "COMPLETED", "solve_status": "UNRESOLVED", "solution_quality": "UNKNOWN",
              "problem": context.problem, "configuration": context.config,
              "problem_hash": context.problem_hash, "config_hash": context.config_hash,
              "attempts": [], "candidate": None, "metric_set": None,
              "diagnostics": [], "observability": {},
              "scope": "SINGLE_ROUTE_LINE_ARC_CORRIDOR_AND_DESIGN_PLANE_GRADES"}
    for index, spec in enumerate(candidate_specs(context)):
        if index >= config["search"]["max_attempts"]:
            result["execution_status"] = "INTERRUPTED"
            result["diagnostics"].append({"code": "ATTEMPT_BUDGET_EXHAUSTED"})
            break
        attempt = {"attempt_index": index, "specification": spec}
        result["attempts"].append(attempt)
        try:
            waypoints = spec.get("waypoints")
            if waypoints is None:
                distances = [s + spec["width_m"] / 2 + spec["offset_m"] for s in edge_setbacks(problem, config)]
                waypoints = inset_waypoints(problem["site"]["boundary"], distances, config["numerics"]["linear_tolerance_m"])
            primitives = fillet_alignment(waypoints, spec["closed"], spec["centerline_radius_m"], config["numerics"]["linear_tolerance_m"])
            samples = sample_alignment(primitives, config["numerics"]["arc_chord_error_m"],
                                       config["vertical"]["sample_step_m"], config["search"]["max_samples"])
            alignment = {"width_m": spec["width_m"], "closed": spec["closed"],
                         "waypoints": waypoints, "primitives": primitives, "samples": samples,
                         "sample_chord_error_bound_m": config["numerics"]["arc_chord_error_m"]}
            horizontal = evaluate_road(context, alignment)
            vertical = evaluate_vertical(context, alignment)
            hard = "VIOLATED" if "VIOLATED" in (horizontal["status"], vertical["status"]) else (
                "NOT_EVALUATED" if vertical["status"] == "NOT_EVALUATED" else "SATISFIED")
            solve = {"SATISFIED": "FEASIBLE", "VIOLATED": "INFEASIBLE", "NOT_EVALUATED": "NOT_EVALUATED"}[hard]
            fingerprint = canonical_hash(alignment)
            candidate = {"candidate_id": "road-" + canonical_hash([context.problem_hash, fingerprint])[:24],
                         "candidate_version": SCRIPT_VERSION, "candidate_fingerprint": fingerprint,
                         "parent_problem_hash": context.problem_hash, "solver_config_hash": context.config_hash,
                         "solver_strategy": spec["strategy"], "solver_strategy_version": SCRIPT_VERSION,
                         "execution_status": "COMPLETED", "solve_status": solve,
                         "solution_quality": "GOOD" if solve == "FEASIBLE" else "UNKNOWN",
                         "hard_constraint_status": hard, "alignment": alignment,
                         "horizontal": horizontal, "vertical": vertical,
                         "feasibility_scope": "THIS_CONSTRUCTED_CANDIDATE_ONLY"}
            attempt.update({"status": solve, "candidate_id": candidate["candidate_id"],
                            "violations": [x for x in horizontal["constraints"] + vertical["constraints"] if x["status"] == "VIOLATED"]})
            result["candidate"] = candidate
            if solve == "FEASIBLE":
                result["solve_status"], result["solution_quality"] = "FEASIBLE", "GOOD"
                break
        except SearchLimit as exc:
            attempt.update({"status": "UNRESOLVED", "reason": str(exc)})
            result["execution_status"] = "INTERRUPTED"
            result["diagnostics"].append({"code": "SAMPLING_BUDGET_EXHAUSTED", "detail": str(exc)})
            break
        except AlignmentUnavailable as exc:
            attempt.update({"status": "UNRESOLVED", "reason": str(exc)})
    if result["candidate"] is not None:
        result["metric_set"] = calculate_road_metrics(context, result["candidate"])
    if result["solve_status"] != "FEASIBLE":
        result["diagnostics"].append({"code": "BASELINE_DID_NOT_FIND_FEASIBLE_SOLUTION",
                                      "detail": "Rejected candidates do not prove that the whole site problem is infeasible."})
    result["observability"] = {"total_ms": (time.perf_counter() - start) * 1000, "attempt_count": len(result["attempts"])}
    schema = read_json(Path(__file__).parent / "schemas/sc_tse_road_result_v0001.schema.json")
    jsonschema.Draft202012Validator(schema).validate(result)
    return result


# ============================================================
# CLI / main 接口区
# ============================================================

class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise RoadError(message)


def _build_parser():
    parser = _Parser(description="SC-TSE Phase4 road corridor, ring and vertical baseline")
    parser.add_argument("--problem", required=True)
    parser.add_argument("--config", default=str(project_root() / DEFAULT_CONFIG_PATH))
    parser.add_argument("--output")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv=None) -> int:
    try:
        args = _build_parser().parse_args(argv)
        result = run_phase4(read_json(args.problem), read_json(args.config), args.dry_run)
        if args.output and not args.dry_run:
            write_new_json(Path(args.output), result)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
        return 4 if result.get("execution_status") == "INTERRUPTED" else 0
    except (RoadError, OSError, jsonschema.ValidationError) as exc:
        print(json.dumps({"status": "error", "error_type": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False))
        return 2
    except Exception as exc:
        print(json.dumps({"status": "error", "error_type": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

