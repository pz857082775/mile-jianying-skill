# ruff: noqa: E402

import json
import os
import sys
import unittest
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
SCRIPTS = CURRENT_DIR.parent / "scripts"
for entry in (str(SCRIPTS), str(CURRENT_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from test_mile import MileBase, make_draft

from mile import memory as m
from mile import profile as profile_mod
from mile.errors import MileError


class ProfileTests(MileBase):
    def test_create_list_show(self):
        p = profile_mod.create_profile("科技评论", draft_path=str(self.draft.folder))
        self.assertEqual(p["name"], "科技评论")
        self.assertEqual(p["version"], 1)

        items = profile_mod.list_profiles()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["name"], "科技评论")

        detail = profile_mod.get_profile("科技评论")
        self.assertIn("style", detail)
        self.assertEqual(detail["style"]["fps"], 30.0)

    def test_duplicate_rejected_without_force(self):
        profile_mod.create_profile("科技评论", style={"fps": 30})
        with self.assertRaises(MileError) as ctx:
            profile_mod.create_profile("科技评论", style={"fps": 60})
        self.assertEqual(ctx.exception.code, "PROFILE_EXISTS")

    def test_force_bumps_version_and_backs_up(self):
        profile_mod.create_profile("科技评论", style={"fps": 30})
        p2 = profile_mod.create_profile("科技评论", style={"fps": 60}, force=True)
        self.assertEqual(p2["version"], 2)
        versions_dir = profile_mod.profile_dir() / "versions"
        self.assertTrue(versions_dir.exists())
        # 旧版备份存在
        backups = list(versions_dir.glob("科技评论.v1.json"))
        self.assertEqual(len(backups), 1)

    def test_update_keeps_style_merge(self):
        profile_mod.create_profile("品牌", style={"fps": 30, "caption": {"font_size": 8}})
        profile_mod.update_profile("品牌", style={"caption": {"color": "#FFD400"}})
        detail = profile_mod.get_profile("品牌")
        self.assertEqual(detail["version"], 2)
        self.assertEqual(detail["style"]["caption"]["color"], "#FFD400")
        self.assertEqual(detail["style"]["caption"]["font_size"], 8)

    def test_export_import_roundtrip(self):
        profile_mod.create_profile("迁移测试", style={"fps": 30}, tags=["a"])
        out = Path(self.tmp.name) / "profile.json"
        profile_mod.export_profile("迁移测试", out)
        profile_mod.delete_profile("迁移测试")
        with self.assertRaises(MileError):
            profile_mod.get_profile("迁移测试")
        profile_mod.import_profile(out)
        detail = profile_mod.get_profile("迁移测试")
        self.assertEqual(detail["style"]["fps"], 30)
        self.assertEqual(detail["tags"], ["a"])

    def test_import_rejects_bad_format(self):
        bad = Path(self.tmp.name) / "bad.json"
        bad.write_text('{"kind": "other"}', encoding="utf-8")
        with self.assertRaises(MileError) as ctx:
            profile_mod.import_profile(bad)
        self.assertEqual(ctx.exception.code, "IMPORT_BAD_FORMAT")

    def test_not_found_error(self):
        with self.assertRaises(MileError) as ctx:
            profile_mod.get_profile("不存在的档案")
        self.assertEqual(ctx.exception.code, "PROFILE_NOT_FOUND")

    def test_slug_sanitizes_name(self):
        p = profile_mod.create_profile('探店/品牌 A-b', style={"fps": 30})
        # 文件名只保留安全字符
        self.assertFalse(p["path"].endswith("探店/品牌 A-b.json"))


class MemoryMigrationTests(MileBase):
    def _two_distinct_drafts(self):
        d1 = make_draft(self.root, "DraftA")
        d2 = make_draft(self.root, "DraftB")
        path = d2 / "draft_content.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["materials"]["texts"][0]["content"] = json.dumps(
            {"text": "第二条完全不同的字幕", "styles": [{"fill": {"content": {"solid": {"color": [1, 0, 0]}}}, "range": [0, 8]}]},
            ensure_ascii=False,
        )
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return d1, d2

    def test_export_import_roundtrip_merge(self):
        d1, d2 = self._two_distinct_drafts()
        m.record_session(project="AAA", draft_path=str(d1), context={"intent": "第一组"}, outcome="accepted")
        m.record_session(project="BBB", draft_path=str(d2), context={"intent": "第二组"}, outcome="rejected")

        out = Path(self.tmp.name) / "mem.json"
        exported = m.export_memory(out)
        self.assertEqual(exported["exported"], 2)

        # 模拟换机：清空后导入
        m._records_path().unlink(missing_ok=True)
        self.assertEqual(m.stats()["count"], 0)
        result = m.import_memory(out, mode="merge")
        self.assertEqual(result["added"], 2)
        self.assertEqual(m.stats()["count"], 2)

        # 重复导入全部跳过
        result2 = m.import_memory(out, mode="merge")
        self.assertEqual(result2["skipped"], 2)
        self.assertEqual(m.stats()["count"], 2)

    def test_import_overwrite_updates(self):
        d1 = make_draft(self.root, "DraftA")
        m.record_session(project="AAA", draft_path=str(d1), context={"intent": "原文", "notes": "v1"})
        out = Path(self.tmp.name) / "mem.json"
        m.export_memory(out)
        # 修改导出文件中的一条记录（list_memory 返回 context，故改 context 内字段）
        payload = json.loads(out.read_text(encoding="utf-8"))
        payload["records"][0]["context"]["notes"] = "v2-modified"
        out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        result = m.import_memory(out, mode="merge", overwrite=True)
        self.assertEqual(result["updated"], 1)
        records = m.list_memory()
        self.assertEqual(records[0]["context"].get("notes"), "v2-modified")

    def test_bad_json_rejected(self):
        bad = Path(self.tmp.name) / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        before = m.stats()["count"]
        with self.assertRaises(MileError) as ctx:
            m.import_memory(bad)
        self.assertEqual(ctx.exception.code, "IMPORT_BAD_FORMAT")
        self.assertEqual(m.stats()["count"], before)  # 原记忆不受影响

    def test_rejected_downweights_recall(self):
        d1, d2 = self._two_distinct_drafts()
        r1 = m.record_session(project="科技评论", draft_path=str(d1), context={"intent": "科技产品 30 秒竖屏"}, outcome="accepted", rating=5)
        r2 = m.record_session(project="科技评论", draft_path=str(d2), context={"intent": "美食探店 60 秒竖屏"}, outcome="rejected", rating=1)
        result = m.recommend_style(context={"intent": "科技"}, k=3)
        scores = {p["id"]: p["score"] for p in result["profiles"]}
        self.assertIn(r1["id"], scores)
        self.assertIn(r2["id"], scores)
        self.assertGreater(scores[r1["id"]], scores[r2["id"]])


if __name__ == "__main__":
    unittest.main()