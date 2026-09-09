# ============================================================
# 文件名: run_sc_tse_orchestrator_v0004.py
# 中文名: SC-TSE RoadGraph 工作流编排入口
# 版本号: v0004
#
# 主层级: action
# 层级: development / sc_tse / external_orchestrator
# 脚本定位: 在 v0003 DAG/状态机上接入 Layout 到 RoadGraph 的真实数据流
#
# 职责说明:
# - 扩展外部节点适配，不改变 Core 与既有状态机职责
#
# 本脚本做什么:
# - 接入 BasicRoadNetwork、RoadGraph Phase 4 和 RoadGraph Access Provider
#
# 本脚本不做什么:
# - 不实现求解算法，不把编排权放入 Core，不改变 v0003 工作流语义
#
# 制度边界声明:
# - 必需 Provider 或 Phase 4 失败继续由 stage admission 阻断最终完成
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: run_sc_tse_orchestrator_v0004
# family: run_sc_tse_orchestrator
# role: road_graph_workflow_dag_runner
# version: v0004
# status: active
# entry_point: scripts/action/development/scripts/sc_tse/run_sc_tse_orchestrator_v0004.py
# input:
#   - v0003-compatible workflow DAG definition or resumable state
# output:
#   - workflow state with RoadGraph provenance and admission propagation
# depends_on:
#   - run_sc_tse_orchestrator_v0003
#   - sc_tse_basic_road_network_generator_v0001
#   - run_sc_tse_road_graph_phase4_v0001
#   - run_sc_tse_road_graph_access_v0001
# used_by:
#   - test_sc_tse_basic_road_network_v0001
# ============================================================

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

import run_sc_tse_orchestrator_v0003 as orchestration_v3
from run_sc_tse_road_graph_access_v0001 import validate_road_graph_access
from run_sc_tse_road_graph_phase4_v0001 import run_road_graph_phase4
from sc_tse_basic_road_network_generator_v0001 import generate_basic_road_network

DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "run_sc_tse_orchestrator"
SCRIPT_NAME = "run_sc_tse_orchestrator_v0004"
SCRIPT_VERSION = "v0004"
RUN_VERSION = "sc-tse-workflow-run-v0.1.0"


# ============================================================
# 工具函数区
# ============================================================

def _resolved_arguments(node: Mapping[str, Any], state: Mapping[str, Any]) -> dict[str, Any]:
    return {key: orchestration_v3._resolve(value, state) for key, value in node["arguments"].items()}


# ============================================================
# 核心类
# ============================================================

def _node_runner_v4(node: dict[str, Any], state: Mapping[str, Any], dry_run: bool) -> dict[str, Any]:
    arguments = _resolved_arguments(node, state)
    provider_id = arguments.get("provider_id")
    if provider_id == "BASIC_ROAD_NETWORK":
        arguments.pop("provider_id")
        phase2_template = arguments.pop("phase2_problem_template")
        layout_result = arguments.pop("layout_result")
        arguments["phase2_problem"] = orchestration_v3._materialize_phase2(phase2_template, layout_result)
        return generate_basic_road_network(**arguments, dry_run=dry_run)
    if provider_id == "ROAD_GRAPH_ACCESS":
        arguments.pop("provider_id")
        phase2_template = arguments.pop("phase2_problem_template")
        layout_result = arguments.pop("layout_result")
        arguments["phase2_problem"] = orchestration_v3._materialize_phase2(phase2_template, layout_result)
        return validate_road_graph_access(**arguments, dry_run=dry_run)
    if node["kind"] == "PHASE4_ENGINEERING" and "road_graph_run" in arguments:
        template = arguments.pop("problem_template")
        layout_result = arguments.pop("layout_result")
        arguments["road_problem_template"] = orchestration_v3._materialize_phase4(template, layout_result)
        return run_road_graph_phase4(**arguments, dry_run=dry_run)
    return orchestration_v3._default_node_runner(node, state, dry_run)


def run_workflow(
    definition: Mapping[str, Any] | None = None,
    *,
    state: Mapping[str, Any] | None = None,
    updates: Mapping[str, Any] | None = None,
    max_node_executions: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    return orchestration_v3.run_workflow(
        definition,
        state=state,
        updates=updates,
        max_node_executions=max_node_executions,
        dry_run=dry_run,
        node_runner=_node_runner_v4,
    )


# ============================================================
# CLI / main 接口区
# ============================================================

def _read(path: str | None) -> dict[str, Any] | None:
    return json.loads(Path(path).read_text(encoding=DEFAULT_ENCODING)) if path else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the SC-TSE RoadGraph workflow DAG.")
    parser.add_argument("--definition")
    parser.add_argument("--state")
    parser.add_argument("--updates")
    parser.add_argument("--max-node-executions", type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    result = run_workflow(
        _read(args.definition), state=_read(args.state), updates=_read(args.updates),
        max_node_executions=args.max_node_executions, dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

