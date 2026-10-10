#!/usr/bin/env python3
"""Focused regression tests using isolated, synthetic exports and vaults."""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


SCRIPT = Path(__file__).with_name("yuque_to_obsidian.py")
SPEC = importlib.util.spec_from_file_location("converter", SCRIPT)
CONVERTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONVERTER)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="yuque-test-")
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.vault = self.root / "vault"
        self.source.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, content):
        target = self.source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
        return target

    def run_converter(self, *arguments, source=None, expected=0):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(source or self.source), str(self.vault), *arguments],
            text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, expected, result.stderr)
        return json.loads(result.stdout) if expected == 0 else result.stderr

    def test_default_dry_run_and_exact_filename(self):
        note = self.write("note.md", "# Note\n")
        plan = self.run_converter("--root-folder", "target", "--output-name", "exact.md", source=note)
        self.assertTrue(plan["dry_run"])
        self.assertEqual(plan["plan"][0]["destination"], "target/exact.md")
        self.assertFalse(self.vault.exists())
        self.run_converter("--apply", "--root-folder", "target", "--output-name", "exact.md", source=note)
        self.assertTrue((self.vault / "target/exact.md").is_file())

    def test_attachment_link_code_and_repeat(self):
        self.write("assets/a.png", b"synthetic-image")
        self.write("assets/manual.pdf", b"synthetic-pdf")
        self.write("docs/next.markdown", "# Next\n")
        self.write("docs/note.md", '# Note\n\n![a](../assets/a.png "Caption")\n'
                   '[manual](../assets/manual.pdf)\n[next](next.markdown#heading)\n'
                   '```md\n![code](../assets/a.png)\n```\n`[example](next.markdown)`\n')
        self.run_converter("--apply", "--root-folder", "folder")
        content = (self.vault / "folder/docs/note.md").read_text()
        self.assertIn('"Caption"', content)
        self.assertIn("next.md#heading", content)
        self.assertIn("```md\n![code](../assets/a.png)\n```", content)
        self.assertIn("`[example](next.markdown)`", content)
        first_assets = set((self.vault / "attachments").iterdir())
        self.assertEqual(len(first_assets), 2)
        self.run_converter("--apply", "--overwrite", "--root-folder", "folder")
        self.assertEqual(first_assets, set((self.vault / "attachments").iterdir()))

    def test_conflicts_do_not_copy_assets_or_modify_note(self):
        self.write("note.md", "![a](a.png)\n")
        self.write("a.png", b"image")
        self.vault.mkdir()
        target = self.vault / "note.md"
        target.write_text("Keep this")
        summary = self.run_converter("--apply")
        self.assertEqual(summary["conflicts"], 1)
        self.assertEqual(summary["attachments"], 0)
        self.assertEqual(target.read_text(), "Keep this")
        self.assertFalse((self.vault / "attachments").exists())

    def test_frontmatter_preserved(self):
        original = '---\n"title": Existing\nvalues: [one, two]\nlink: "[other](other.markdown)"\n...\n# Title\n'
        self.write("note.md", original)
        self.run_converter("--apply")
        self.assertEqual((self.vault / "note.md").read_text(), original)

    def test_identical_attachment_sources_share_one_file(self):
        self.write("a/asset.png", b"same")
        self.write("b/asset.png", b"same")
        self.write("note.md", "![a](a/asset.png)\n![b](b/asset.png)\n")
        self.run_converter("--apply")
        self.assertEqual(len(list((self.vault / "attachments").iterdir())), 1)

    def test_single_note_explicit_asset_boundary(self):
        note = self.write("docs/note.md", "![a](../assets/a.png)\n")
        self.write("assets/a.png", b"image")
        summary = self.run_converter(source=note)
        self.assertEqual(summary["missing_assets"], 1)
        summary = self.run_converter("--asset-root", str(self.source), source=note)
        self.assertEqual(summary["attachments"], 1)
        self.assertFalse(self.vault.exists())

    def test_zip_and_traversal(self):
        archive = self.root / "export.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("notes/note.md", "# Note\n")
        self.run_converter("--apply", source=archive)
        self.assertTrue((self.vault / "notes/note.md").exists())
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("../escape.md", "unsafe")
        self.assertIn("unsafe ZIP", self.run_converter(source=archive, expected=2))
        self.assertFalse((self.root / "escape.md").exists())

    def test_symlink_escape_and_invalid_scope(self):
        self.write("note.md", "# Note\n")
        outside = self.root / "outside"
        outside.mkdir()
        self.vault.mkdir()
        (self.vault / "target").symlink_to(outside, target_is_directory=True)
        self.assertIn("escapes vault", self.run_converter("--apply", "--root-folder", "target", expected=2))
        self.assertFalse(list(outside.iterdir()))
        self.run_converter("--root-folder", "../outside", expected=2)
        self.run_converter("--output-name", "folder/note.md", expected=2)
        self.write("another.md", "# Another\n")
        self.run_converter("--output-name", "note.md", expected=2)

    def test_html_code_lists_table_and_ignore_scripts(self):
        source = self.write("note.html", '<html><head><title>Noise</title></head><body>'
                            '<h1>Title</h1><ol><li>First<ul><li>Child</li></ul></li><li>Second</li></ol>'
                            '<pre><code>line 1\n\n\nline 2  \n</code></pre>'
                            '<table><tr><td>Cell</td></tr></table><script>secret()</script></body></html>')
        self.run_converter("--apply", source=source)
        content = (self.vault / "note.md").read_text()
        self.assertIn("1. First", content)
        self.assertIn("2. Second", content)
        self.assertIn("line 1\n\n\nline 2  \n```", content)
        self.assertIn("<table>", content)
        self.assertNotIn("secret()", content)
        self.assertNotIn("Noise", content)


if __name__ == "__main__":
    unittest.main()
