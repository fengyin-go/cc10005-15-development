"""增量区间认领的领域规则测试：链式推进、幂等回执、双轨去重与水位可比性。"""
from __future__ import annotations

import unittest

from app.services.delta import DeltaService
from app.store import store


class DeltaIntervalsTest(unittest.TestCase):
    def setUp(self) -> None:
        store.rows("delta").clear()
        self.service = DeltaService()

    def add_cursor(self, **over):
        rows = store.rows("delta")
        entry = {
            "id": len(rows) + 1,
            "status": "运行中",
            "pending": False,
            "abnormal": False,
            "游标编号": f"DELT-T{len(rows) + 1}",
            "目标站点": "测试站",
            "增量字段": "更新时间",
            "当前水位": "1000",
            "回退步数": "100",
            "批次大小": "100",
            "去重窗口": "50",
            "重跑开关": "开",
            "水位类型": "数值",
            "水位顺序表": [],
            "claims": [],
            "segments": [],
            "samples": [],
            "applied_receipts": [],
        }
        entry.update(over)
        rows.append(entry)
        return entry

    # ------------------------------------------------------------ 水位可比性
    def test_incomparable_text_watermark_rejected(self):
        """旧样例水位是不可比较文字：先要求明确类型与顺序，不能凭字典序认领。"""
        entry = self.add_cursor(当前水位="增量采集样例3", 水位类型="")
        claim, message = self.service.create_claim(entry["id"], "realtime", "增量采集样例3", "增量采集样例9", None)
        self.assertIsNone(claim)
        self.assertIn("明确水位类型与顺序", message)
        self.assertIn("字典序", message)

    def test_ordered_text_requires_explicit_order(self):
        """有序文本必须给出显式顺序表；顺序表外的值不可认领。"""
        entry = self.add_cursor(当前水位="批次-甲", 水位类型="有序文本", 水位顺序表=[])
        _, message, ok = self.service.set_watermark(entry["id"], "有序文本", None, [])
        self.assertFalse(ok)
        self.assertIn("顺序表", message)
        _, message, ok = self.service.set_watermark(entry["id"], "有序文本", None, ["批次-甲", "批次-乙", "批次-丙"])
        self.assertTrue(ok)
        claim, message = self.service.create_claim(entry["id"], "realtime", "批次-甲", "批次-丙", None)
        self.assertIsNotNone(claim)
        claim, message = self.service.create_claim(entry["id"], "realtime", "批次-丙", "批次-丁", None)
        self.assertIsNone(claim)
        self.assertIn("无法按", message)

    def test_claim_requires_running_cursor(self):
        entry = self.add_cursor(status="待校验")
        claim, message = self.service.create_claim(entry["id"], "realtime", "1000", "1100", None)
        self.assertIsNone(claim)
        self.assertIn("启动增量", message)

    # ------------------------------------------------------------ 链式认领
    def test_realtime_chain_no_gap_no_overlap(self):
        """相邻边界可并行认领；留间隙或重叠的认领被拦下。"""
        entry = self.add_cursor()
        c1, _ = self.service.create_claim(entry["id"], "realtime", "1000", "1100", None)
        self.assertIsNotNone(c1)
        c2, message = self.service.create_claim(entry["id"], "realtime", "1100", "1200", None)
        self.assertIsNotNone(c2, message)
        gap, message = self.service.create_claim(entry["id"], "realtime", "1150", "1250", None)
        self.assertIsNone(gap)
        self.assertIn("预留前沿", message)
        overlap, message = self.service.create_claim(entry["id"], "realtime", "1050", "1150", None)
        self.assertIsNone(overlap)
        empty, message = self.service.create_claim(entry["id"], "realtime", "1200", "1200", None)
        self.assertIsNone(empty)
        self.assertIn("不能为空", message)

    def test_inflight_segment_cannot_be_passed(self):
        """在途段未确认前，后续认领不能先确认；按序确认后区间无缝无重叠。"""
        entry = self.add_cursor()
        c1, _ = self.service.create_claim(entry["id"], "realtime", "1000", "1100", None)
        c2, _ = self.service.create_claim(entry["id"], "realtime", "1100", "1200", None)
        _, message, _ = self.service.confirm_claim(entry["id"], c2["id"], {"批次号": "B2"})
        self.assertIn("前序", message)
        self.assertEqual(entry["当前水位"], "1000")
        _, message, _ = self.service.confirm_claim(entry["id"], c1["id"], {"批次号": "B1", "实际终点": "1100"})
        self.assertEqual(entry["当前水位"], "1100")
        _, message, _ = self.service.confirm_claim(entry["id"], c2["id"], {"批次号": "B2"})
        self.assertEqual(entry["当前水位"], "1200")
        spans = [(s["start"], s["end"]) for s in entry["segments"]]
        self.assertEqual(spans, [("1000", "1100"), ("1100", "1200")])

    def test_actual_end_must_stay_inside_claim(self):
        entry = self.add_cursor()
        c1, _ = self.service.create_claim(entry["id"], "realtime", "1000", "1100", None)
        _, message, _ = self.service.confirm_claim(entry["id"], c1["id"], {"批次号": "B1", "实际终点": "1150"})
        self.assertIn("认领区间", message)
        _, message, _ = self.service.confirm_claim(entry["id"], c1["id"], {"批次号": "B1", "实际终点": "1060"})
        self.assertEqual(entry["当前水位"], "1060")

    # ------------------------------------------------------------ 幂等回执
    def test_duplicate_receipt_not_reapplied(self):
        """重复回执不额外前移：同批次号再次确认是幂等空操作。"""
        entry = self.add_cursor()
        c1, _ = self.service.create_claim(entry["id"], "realtime", "1000", "1100", None)
        _, message, _ = self.service.confirm_claim(entry["id"], c1["id"], {"批次号": "B1"})
        self.assertEqual(entry["当前水位"], "1100")
        c2, _ = self.service.create_claim(entry["id"], "realtime", "1100", "1200", None)
        entry2, message, stats = self.service.confirm_claim(entry["id"], c2["id"], {"批次号": "B1"})
        self.assertIsNotNone(entry2)
        self.assertIn("重复回执", message)
        self.assertTrue(stats.get("重复回执"))
        self.assertEqual(entry["当前水位"], "1100")
        self.assertEqual(c2["status"], "在途")
        _, message, _ = self.service.confirm_claim(entry["id"], c1["id"], {"批次号": "B1"})
        self.assertIn("重复回执", message)
        self.assertEqual(entry["当前水位"], "1100")
        _, message, _ = self.service.confirm_claim(entry["id"], c2["id"], {"批次号": "B2"})
        self.assertEqual(entry["当前水位"], "1200")

    # ------------------------------------------------------------ 暂停与续做
    def test_pause_keeps_claim_and_resume_continues(self):
        """暂停保留认领及候选终点；暂停段仍占据前沿，续做后从该段接着取数。"""
        entry = self.add_cursor()
        c1, _ = self.service.create_claim(entry["id"], "realtime", "1000", "1100", None)
        _, message = self.service.pause_claim(entry["id"], c1["id"])
        self.assertIn("候选终点", message)
        self.assertEqual(c1["status"], "已暂停")
        self.assertEqual(c1["end"], "1100")
        c2, message = self.service.create_claim(entry["id"], "realtime", "1100", "1200", None)
        self.assertIsNotNone(c2, message)
        _, message, _ = self.service.confirm_claim(entry["id"], c1["id"], {"批次号": "B1"})
        self.assertIn("续做", message)
        _, message = self.service.resume_claim(entry["id"], c1["id"])
        self.assertIn("接着取数", message)
        _, message, _ = self.service.confirm_claim(entry["id"], c1["id"], {"批次号": "B1"})
        self.assertEqual(entry["当前水位"], "1100")

    def test_cursor_pause_cascades_to_claims(self):
        entry = self.add_cursor()
        c1, _ = self.service.create_claim(entry["id"], "realtime", "1000", "1100", None)
        _, message = self.service.run_action(entry["id"], "暂停增量")
        self.assertIn("保留认领及候选终点", message)
        self.assertEqual(c1["status"], "已暂停")
        _, message = self.service.run_action(entry["id"], "续做增量")
        self.assertIn("接着取数", message)
        self.assertEqual(c1["status"], "在途")
        self.assertEqual(entry["status"], "运行中")
        bad, message = self.service.run_action(entry["id"], "启动增量")
        self.assertIsNone(bad)
        self.assertIn("不能执行", message)

    # ------------------------------------------------------------ 回扫与去重
    def test_backfill_start_from_backtrack_steps(self):
        """回扫起点 = 当前水位 - 回退步数；回扫不推进实时水位。"""
        entry = self.add_cursor(当前水位="1000", 回退步数="100")
        claim, message = self.service.create_claim(entry["id"], "backfill", None, None, None)
        self.assertIsNotNone(claim, message)
        self.assertEqual((claim["start"], claim["end"]), ("900", "1000"))
        dup, message = self.service.create_claim(entry["id"], "backfill", None, None, None)
        self.assertIsNone(dup)
        self.assertIn("重叠", message)
        _, message, stats = self.service.confirm_claim(entry["id"], claim["id"], {"批次号": "BF1"})
        self.assertEqual(entry["当前水位"], "1000")
        self.assertEqual(stats["迟到补录"] + stats["重复"], sum(stats.values()))

    def test_backfill_dedup_against_realtime_track(self):
        """回扫样本与实时轨同键即判重；未见过的回扫样本按迟到补录进入结果。"""
        entry = self.add_cursor(当前水位="1000", 回退步数="100", 去重窗口="50")
        c1, _ = self.service.create_claim(entry["id"], "realtime", "1000", "1100", None)
        _, _, stats = self.service.confirm_claim(entry["id"], c1["id"], {"批次号": "RT1"})
        self.assertEqual(stats["正常"], 50)
        self.assertEqual(entry["当前水位"], "1100")
        bf1, _ = self.service.create_claim(entry["id"], "backfill", None, None, None)
        self.assertEqual((bf1["start"], bf1["end"]), ("1000", "1100"))
        _, _, stats = self.service.confirm_claim(entry["id"], bf1["id"], {"批次号": "BF1"})
        self.assertEqual(stats["重复"], 50)
        self.assertEqual(stats["迟到补录"], 0)
        self.assertEqual(entry["当前水位"], "1100")
        entry["回退步数"] = "200"
        bf2, _ = self.service.create_claim(entry["id"], "backfill", None, None, None)
        self.assertEqual((bf2["start"], bf2["end"]), ("900", "1100"))
        _, _, stats = self.service.confirm_claim(entry["id"], bf2["id"], {"批次号": "BF2"})
        self.assertEqual(stats["重复"], 25)
        self.assertEqual(stats["迟到补录"], 25)

    def test_late_sample_still_accepted(self):
        """迟到样本仍有进入结果的机会：水位落后于前沿但未重复时按迟到补录入账。"""
        entry = self.add_cursor()
        c1, _ = self.service.create_claim(entry["id"], "realtime", "1000", "1100", None)
        receipt = {"批次号": "B1", "样本": [
            {"样本键": "迟到-1", "水位": "950"},
            {"样本键": "正常-1", "水位": "1050"},
        ]}
        _, message, stats = self.service.confirm_claim(entry["id"], c1["id"], receipt)
        self.assertEqual(stats["迟到补录"], 1)
        self.assertEqual(stats["正常"], 1)
        verdicts = {s["样本键"]: s["判定"] for s in entry["samples"]}
        self.assertEqual(verdicts["迟到-1"], "迟到补录")

    def test_dedup_window_boundary(self):
        """去重窗口按水位距离判定：窗口内同键判重，窗口外仍可入账。"""
        entry = self.add_cursor(去重窗口="50")
        c1, _ = self.service.create_claim(entry["id"], "realtime", "1000", "1100", None)
        self.service.confirm_claim(entry["id"], c1["id"], {"批次号": "B1", "样本": [{"样本键": "K", "水位": "1000"}]})
        c2, _ = self.service.create_claim(entry["id"], "realtime", "1100", "1200", None)
        _, _, stats = self.service.confirm_claim(entry["id"], c2["id"], {"批次号": "B2", "样本": [{"样本键": "K", "水位": "1040"}]})
        self.assertEqual(stats["重复"], 1)
        c3, _ = self.service.create_claim(entry["id"], "realtime", "1200", "1300", None)
        _, _, stats = self.service.confirm_claim(entry["id"], c3["id"], {"批次号": "B3", "样本": [{"样本键": "K", "水位": "1100"}]})
        self.assertEqual(stats["迟到补录"], 1)

    # ------------------------------------------------------------ 观察窗口总览
    def test_intervals_view_shape(self):
        """区间总览给出双轨分段、关联作业与坐标，供观察窗口检查。"""
        entry = self.add_cursor()
        c1, _ = self.service.create_claim(entry["id"], "realtime", "1000", "1100", None)
        self.service.confirm_claim(entry["id"], c1["id"], {"批次号": "B1"})
        self.service.create_claim(entry["id"], "realtime", "1100", "1200", None)
        self.service.create_claim(entry["id"], "backfill", None, None, None)
        view = self.service.get_intervals(entry["id"])
        self.assertTrue(view["可比较"])
        self.assertEqual(view["预留前沿"], "1200")
        confirmed = view["tracks"]["realtime"]["confirmed"]
        inflight = view["tracks"]["realtime"]["claims"]
        coverage = view["tracks"]["backfill"]["claims"]
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(confirmed[0]["job"], c1["job"])
        self.assertEqual(confirmed[0]["批次号"], "B1")
        self.assertEqual(len(inflight), 1)
        self.assertEqual(len(coverage), 1)
        for seg in confirmed + inflight + coverage:
            self.assertLess(seg["x0"], seg["x1"])
            self.assertTrue(seg["job"])
        self.assertLess(view["axis"]["min"], view["axis"]["max"])

    def test_intervals_view_marks_incomparable(self):
        entry = self.add_cursor(当前水位="增量采集样例3", 水位类型="")
        view = self.service.get_intervals(entry["id"])
        self.assertFalse(view["可比较"])
        self.assertIn("明确水位类型与顺序", view["不可比较原因"])


if __name__ == "__main__":
    unittest.main()
