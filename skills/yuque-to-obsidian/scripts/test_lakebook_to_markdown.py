#!/usr/bin/env python3
"""Lakebook staging regression tests with synthetic archives."""

import io
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("lakebook_to_markdown.py")


class LakebookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lakebook-test-")
        self.root = Path(self.temp.name)
        self.archive = self.root / "知识库.lakebook"
        self.destination = self.root / "staging"

    def tearDown(self):
        self.temp.cleanup()

    def make_archive(self, docs=None, extra=None):
        docs = docs or [
            {"slug": "plan", "title": "学习计划", "status": 1, "format": "lake",
             "body": "<h2>第一天</h2><ul><li>阅读文档</li></ul>"},
            {"slug": "blank", "title": "无标题", "status": 0, "format": "lake", "body": ""},
        ]
        meta = {"book": {"path": "https://www.yuque.com/example/book"}, "docs": [
            {k: d[k] for k in ("slug", "title", "status")} for d in docs
        ]}
        members = {"bundle/$meta.json": {"meta": json.dumps(meta, ensure_ascii=False)}}
        members.update({"bundle/" + d["slug"] + ".json": {"doc": d} for d in docs})
        with tarfile.open(self.archive, "w") as archive:
            for name, value in members.items():
                data = json.dumps(value, ensure_ascii=False).encode("utf-8")
                item = tarfile.TarInfo(name)
                item.size = len(data)
                archive.addfile(item, io.BytesIO(data))
            if extra:
                item = tarfile.TarInfo(extra)
                item.size = 5
                archive.addfile(item, io.BytesIO(b"asset"))

    def run_stage(self, *args, success=True):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(self.archive), str(self.destination), *args],
            text=True, capture_output=True,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0)
        return result.stderr

    def test_chinese_body_metadata_and_empty_draft(self):
        self.make_archive()
        report = self.run_stage()
        self.assertEqual(len(report), 2)
        content = (self.destination / "学习计划.md").read_text(encoding="utf-8")
        self.assertIn("## 第一天", content)
        self.assertIn("- 阅读文档", content)
        self.assertIn("https://www.yuque.com/example/book/plan", content)
        self.assertEqual(report[1]["body_chars"], 0)
        self.assertEqual(report[1]["status"], 0)
        self.assertEqual(report[1]["content_source"], "empty")
        self.assertTrue((self.destination / "无标题.md").is_file())

    def test_slug_filter_and_unknown_slug(self):
        self.make_archive()
        report = self.run_stage("--slug", "plan")
        self.assertEqual(len(report), 1)
        self.assertFalse((self.destination / "无标题.md").exists())
        self.destination = self.root / "unknown"
        self.assertIn("Document slug not in Lakebook", self.run_stage("--slug", "missing", success=False))
        self.assertFalse(self.destination.exists())

    def test_duplicate_titles_and_unsafe_slug(self):
        docs = [{"slug": slug, "title": "同名", "status": 1, "body": "<p>正文</p>"} for slug in ("a", "b")]
        self.make_archive(docs)
        self.run_stage()
        self.assertTrue((self.destination / "同名.md").exists())
        self.assertTrue((self.destination / "同名-2.md").exists())
        self.make_archive([{"slug": "../escape", "title": "危险", "status": 1, "body": "<p>x</p>"}])
        self.destination = self.root / "unsafe"
        self.assertIn("Unsafe document slug", self.run_stage(success=False))
        self.assertFalse(self.destination.exists())

    def test_nonempty_staging_is_never_overwritten(self):
        self.make_archive()
        self.destination.mkdir()
        note = self.destination / "existing.md"
        note.write_text("保留", encoding="utf-8")
        self.assertIn("must be empty", self.run_stage(success=False))
        self.assertEqual(note.read_text(encoding="utf-8"), "保留")
        self.assertEqual(len(list(self.destination.iterdir())), 1)

    def test_extra_assets_block_incomplete_staging(self):
        self.make_archive(extra="bundle/assets/image.png")
        self.assertIn("non-JSON assets", self.run_stage(success=False))
        self.assertFalse(self.destination.exists())

    def test_editor_only_body_blocks_silent_data_loss(self):
        self.make_archive([{"slug": "a", "title": "计划", "status": 1, "body": "", "body_asl": "<doc>内容</doc>"}])
        self.assertIn("no HTML body", self.run_stage(success=False))
        self.assertFalse(self.destination.exists())

    def test_body_draft_fallback_and_differing_draft(self):
        self.make_archive([
            {"slug": "a", "title": "草稿", "status": 0, "body": "", "body_draft": "<p>未发布</p>"},
            {"slug": "b", "title": "已发布", "status": 1, "body": "<p>正式</p>", "body_draft": "<p>修改稿</p>"},
        ])
        report = self.run_stage()
        self.assertEqual(report[0]["content_source"], "body_draft")
        self.assertTrue(report[1]["draft_differs"])
        self.assertIn("正式", (self.destination / "已发布.md").read_text(encoding="utf-8"))
        self.assertNotIn("修改稿", (self.destination / "已发布.md").read_text(encoding="utf-8"))

    def test_unknown_format_is_rejected(self):
        self.make_archive([{"slug": "a", "title": "计划", "status": 1, "format": "unknown", "body": "data"}])
        self.assertIn("Unsupported document body format", self.run_stage(success=False))
        self.assertFalse(self.destination.exists())


if __name__ == "__main__":
    unittest.main()
