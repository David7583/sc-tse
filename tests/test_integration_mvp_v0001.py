# ============================================================
# 文件名: test_integration_mvp_v0001.py
# 中文名: SC-TSE Integration MVP 接口验收
# 版本号: v0001
#
# 主层级: action
# 层级: staging / sc_tse_integration_mvp / tests
# 脚本定位: 验证唯一主链、坐标修改和失败语义
#
# 职责说明:
# - 验证真实成功、失败、恢复与输入安全
#
# 本脚本做什么:
# - 执行接口和原主链基准摘要的行为对比
#
# 本脚本不做什么:
# - 不修改原始案例、Core、配置或数据库
#
# 制度边界声明:
# - 中间文件由被测接口写入临时目录
# - 验收日志由调用方保存在发布目录外
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: test_integration_mvp_v0001
# family: test_integration_mvp
# role: integration_acceptance_test
# version: v0001
# status: experimental
# entry_point: tests/test_integration_mvp_v0001.py
# input:
#   - SC_TSE_TEST_FRONTEND optional target directory
# output:
#   - unittest results
# depends_on:
#   - sc_tse_integration_api_v0001
# used_by: []
# ============================================================

from __future__ import annotations

import copy
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "test_integration_mvp"
SCRIPT_NAME = "test_integration_mvp_v0001"
SCRIPT_VERSION = "v0001"
STAGE = Path(__file__).resolve().parent
FRONTEND = Path(os.environ.get("SC_TSE_TEST_FRONTEND", STAGE.parent / "scripts/action/development/scripts/sc_tse/frontend"))
sys.path.insert(0, str(FRONTEND))
import sc_tse_integration_api_v0001 as api


# ============================================================
# 工具函数区
# ============================================================

def request(project, y=None):
    return {"schema_version": "sc-tse-integration-request-v0001", "request_id": "acceptance",
            "project": project, "edits": [] if y is None else [{"block_id": "B3_ADMIN", "center_x": 75, "center_y": y}]}


# ============================================================
# 核心测试类
# ============================================================

class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project = api.load_project()
        cls.before = api.digest(cls.project)
        with patch.object(api, "run_workflow", wraps=api.run_workflow) as runner:
            cls.base = api.run_request(request(cls.project))
            assert runner.call_count == 1
            cls.definition = runner.call_args.args[0]
        cls.moved = api.run_request(request(cls.project, 135))
        cls.blocked = api.run_request(request(cls.project, 80))

    def test_unique_complete_chain(self):
        self.assertEqual(self.base["status"], "COMPLETED")
        self.assertEqual(self.base["workflow"]["executed_node_ids"], ["precheck", "layout", "connectivity", "road_graph", "engineering", "access"])
        self.assertTrue(all(n["status"] == "PASSED" for n in self.base["workflow"]["workflow_state"]["nodes"].values()))

    def test_manual_edit_refreshes_real_road_and_access(self):
        self.assertEqual(self.moved["status"], "COMPLETED")
        block = next(b for b in self.moved["scene"]["blocks"] if b["block_id"] == "B3_ADMIN")
        self.assertEqual(block["geometry"]["center"], [75, 135])
        self.assertNotEqual(self.moved["scene"]["road_graph"], self.base["scene"]["road_graph"])
        self.assertEqual(self.moved["metrics"]["access_status"], "PASS")
        self.assertNotEqual(self.moved["results"]["engineering"]["source_refs"]["road_graph_result_hash"], self.base["results"]["engineering"]["source_refs"]["road_graph_result_hash"])

    def test_failure_does_not_reuse_any_downstream_result(self):
        self.assertEqual(self.blocked["status"], "WAITING_FOR_EXTERNAL_UPDATE")
        self.assertEqual(self.blocked["blocking_nodes"][0]["node_id"], "layout")
        self.assertIsNone(self.blocked["results"]["road_graph"])
        self.assertIsNone(self.blocked["results"]["engineering"])
        self.assertIsNone(self.blocked["results"]["access"])
        self.assertEqual(self.blocked["scene"]["engineered_roads"], [])
        self.assertEqual(self.blocked["scene"]["layout_source"], "EXPLICIT_INPUT")

    def test_original_project_immutable_and_gate_offset_stable(self):
        self.assertEqual(api.digest(self.project), self.before)
        changed = api.edited_case(self.project, request(self.project, 135)["edits"])
        block = next(b for b in changed["blocks"] if b["block_id"] == "B3_ADMIN")
        self.assertEqual(block["connection_gates"][0]["position"], [75, 123])

    def test_reference_equivalence(self):
        reference = json.loads((STAGE / "fixtures/reference_summary.json").read_text(encoding=DEFAULT_ENCODING))
        self.assertEqual(reference["status"], self.base["status"])
        self.assertEqual(reference["road_graph_sha256"], api.digest(self.base["scene"]["road_graph"]))
        self.assertEqual(reference["access_status"], self.base["metrics"]["access_status"])

    def test_rejects_bad_coordinates_and_unknown_objects_before_run(self):
        for value in [None, True, float("nan"), float("inf"), 10000001]:
            with self.subTest(value=value), self.assertRaises(api.IntegrationInputError):
                api.run_request(request(self.project, value) if value is not None else {**request(self.project, 135), "edits": [{"block_id":"B3_ADMIN","center_x":None,"center_y":135}]})
        payload = request(self.project, 135)
        payload["edits"][0]["block_id"] = "MISSING"
        with self.assertRaises(api.IntegrationInputError):
            api.run_request(payload)

    def test_rejects_project_path_injection_and_identity_drift(self):
        for field in ["json_path", "file_path", "from_node"]:
            project = copy.deepcopy(self.project)
            project["provenance"][field] = "forbidden"
            with self.assertRaises(api.IntegrationInputError):
                api.run_request(request(project))
        project = copy.deepcopy(self.project)
        project["road_problem_template"]["site"]["site_id"] = "other"
        with self.assertRaises(api.IntegrationInputError):
            api.run_request(request(project))

    def test_fixed_input_and_scene_are_exact_projections(self):
        for corridor, displayed in zip(self.base["results"]["engineering"]["corridor_runs"], self.base["scene"]["engineered_roads"]):
            self.assertEqual([s["xy"] for s in corridor["road_run"]["candidate"]["alignment"]["samples"]], displayed["coordinates"])
        api.validate(self.base, "result")


# ============================================================
# CLI / main 接口区
# ============================================================

if __name__ == "__main__":
    unittest.main(verbosity=2)
