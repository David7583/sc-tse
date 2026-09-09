# ============================================================
# 文件名: sc_tse_road_metrics_v0001.py
# 中文名: 道路工程事实指标与来源绑定
# 版本号: v0001
#
# 主层级: action
# 层级: development / sc_tse / phase4
# 脚本定位: road_metric_provider
#
# 职责说明:
# - 道路工程事实指标与来源绑定
#
# 本脚本做什么:
# - RoadContext and evaluated road candidate → Road SolutionMetricSet with freshness binding
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
# alias: sc_tse_road_metrics_v0001
# family: sc_tse_road_metrics
# role: road_metric_provider
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/sc_tse/sc_tse_road_metrics_v0001.py
# input:
#   - RoadContext and evaluated road candidate
# output:
#   - Road SolutionMetricSet with freshness binding
# depends_on:
#   - sc_tse_road_contracts_v0001
# used_by:
#   - run_sc_tse_phase4_v0001
# ============================================================

from __future__ import annotations

from datetime import datetime, timezone

from sc_tse_road_contracts_v0001 import RoadError, canonical_hash

DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "sc_tse_road_metrics"
SCRIPT_NAME = "sc_tse_road_metrics_v0001"
SCRIPT_VERSION = "v0001"
PROVIDER_ID = "sc_tse.road_geometry_vertical"


# ============================================================
# 数据结构
# ============================================================

# SolutionMetricSet follows existing fact/status/provenance semantics; comparison
# scope ROAD_ENGINEERING is distinct from the legacy LAYOUT_GEOMETRY ranker.


# ============================================================
# 工具函数区
# ============================================================

def binding(context, candidate: dict) -> dict:
    return {"candidate_fingerprint": candidate["candidate_fingerprint"], "parent_problem_hash": context.problem_hash,
            "provider_id": PROVIDER_ID, "provider_version": SCRIPT_VERSION, "provider_config_hash": context.config_hash,
            "engineering_context": context.problem["design_elevation"],
            "relevant_input_refs": {"problem_id": context.problem["problem_id"], "site_id": context.problem["site"]["site_id"]}}


# ============================================================
# 核心类
# ============================================================

def calculate_road_metrics(context, candidate: dict) -> dict:
    fact = candidate["alignment"]
    vertical = candidate["vertical"]
    available = candidate["solve_status"] == "FEASIBLE"
    values = [
        ("engineering_road_length", "m", "MINIMIZE", sum(p["length_m"] for p in fact["primitives"]), "SUM_ANALYTIC_LINE_ARC_LENGTH"),
        ("road_width", "m", "NONE", fact["width_m"], "CONSTANT_PAVEMENT_WIDTH"),
        ("road_cycle_count", "1", "NONE", candidate["horizontal"]["cycle_count"], "SIMPLE_CLOSED_ROUTE"),
        ("maximum_longitudinal_slope", "1", "MINIMIZE", vertical["max_road_edge_grade_ratio"], "ANALYTIC_ROAD_EDGE_GRADE"),
    ]
    context_binding = binding(context, candidate)
    result = {"record_type": "SolutionMetricSet", "comparison_scope": "ROAD_ENGINEERING",
              "candidate_id": candidate["candidate_id"], **context_binding,
              "calculation_context_hash": canonical_hash(context_binding), "metrics": []}
    for metric, unit, direction, value, method in values:
        result["metrics"].append({"metric_id": metric, "unit": unit, "direction": direction,
                                  "status": "CALCULATED" if available and value is not None else "UNRESOLVED",
                                  "value": value if available else None, "method": method,
                                  "provider_id": PROVIDER_ID, "provider_version": SCRIPT_VERSION,
                                  "evidence": {"candidate_id": candidate["candidate_id"],
                                               "constraint_status": candidate["hard_constraint_status"]}})
    for metric, unit in (("vehicle_swept_path_feasibility", "1"), ("earthwork_volume", "m3")):
        result["metrics"].append({"metric_id": metric, "unit": unit, "direction": "NONE",
                                  "status": "NOT_AVAILABLE", "value": None, "method": "PROVIDER_NOT_IMPLEMENTED",
                                  "provider_id": None, "provider_version": None, "evidence": {}})
    result["calculated_at"] = datetime.now(timezone.utc).isoformat()
    return result


def validate_metric_freshness(context, candidate: dict, metrics: dict) -> str:
    actual_fingerprint = canonical_hash(candidate["alignment"])
    if actual_fingerprint != candidate["candidate_fingerprint"]:
        raise RoadError("candidate fingerprint does not match geometry")
    expected = canonical_hash(binding(context, candidate))
    return "CURRENT" if metrics.get("calculation_context_hash") == expected else "STALE"

