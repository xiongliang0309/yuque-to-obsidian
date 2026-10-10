#!/usr/bin/env python3
"""Convert a local Yuque export into an Obsidian vault.

The converter deliberately has no network dependency. It plans all changes first,
and only writes when --apply is supplied.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import posixpath
import re
import shutil
import sys
import tempfile
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit


SUPPORTED_DOCUMENTS = {".md", ".markdown", ".html", ".htm"}
ASSET_RE = re.compile(r"!\[([^\]]*)\]\(\s*(<[^>]+>|[^)\s]+)(?:\s+[^)]*)?\)")
LINK_RE = re.compile(r"(?<!!)(\[[^\]]*\])\(\s*(<[^>]+>|[^)\s]+)(?:\s+[^)]*)?\)")
HTML_IMAGE_RE = re.compile(r"<img\b[^>]*?\bsrc\s*=\s*(['\"])(.*?)\1", re.I)
HEADING_RE = re.compile(r"^\s*#\s+(.+?)\s*$", re.M)
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n(?:---|\.\.\.)\s*\n?", re.S)


def clean_component(value: str) -> str:
    """Make one path component safe without transliterating user text."""
    value = re.sub(r"[\x00-\x1f\x7f]", "", value)
    value = value.replace("/", "-").replace("\\", "-").replace(":", "-").strip()
    value = value.rstrip(".")
    return value or "untitled"


def safe_relative_path(path: str) -> Path:
    candidate = Path(path)
    if "\\" in path or candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"path must be relative and must not contain '..': {path}")
    return Path(*(clean_component(part) for part in candidate.parts if part not in ("", ".")))


def strip_wrapped_url(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == "<" and value[-1] == ">":
        return value[1:-1]
    return value


class HTMLToMarkdown(HTMLParser):
    """Conservative HTML-to-Markdown conversion for common Yuque exports."""

    BLOCKS = {"address", "article", "blockquote", "div", "dl", "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "ol", "p", "pre", "section", "table", "tr", "ul"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.stack: list[tuple[str, dict[str, str]]] = []
        self.ordered_depth = 0
        self.list_numbers: list[int] = []
        self.in_pre = False
        self.in_table_header = False
        self.ignored_depth = 0
        self.table_depth = 0
        self.table_html: list[str] = []
        self.list_types: list[str] = []

    def add(self, text: str) -> None:
        self.parts.append(text)

    def ensure_newline(self, count: int = 2) -> None:
        current = "".join(self.parts)
        needed = "\n" * count
        if not current.endswith(needed):
            self.parts.append("\n" * (count - (len(current) - len(current.rstrip("\n")))))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_dict = {key.lower(): (value or "") for key, value in attrs}
        if tag in {"script", "style", "head"}:
            self.ignored_depth += 1
        if self.ignored_depth:
            return
        if tag == "table" or self.table_depth:
            if tag == "table":
                self.table_depth += 1
            self.table_html.append(self.get_starttag_text())
            return
        if tag in self.BLOCKS:
            self.ensure_newline(2)
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.add("#" * int(tag[1]) + " ")
        elif tag == "br":
            self.add("  \n")
        elif tag == "hr":
            self.add("---")
        elif tag == "a":
            self.add("[")
        elif tag == "img":
            alt = attrs_dict.get("alt", "")
            src = attrs_dict.get("src", "")
            self.add(f"![{alt}]({src})")
        elif tag in {"strong", "b"}:
            self.add("**")
        elif tag in {"em", "i"}:
            self.add("*")
        elif tag in {"del", "s", "strike"}:
            self.add("~~")
        elif tag == "code" and not self.in_pre:
            self.add("`")
        elif tag == "pre":
            self.in_pre = True
            self.add("```\n")
        elif tag == "ul":
            self.list_numbers.append(0)
            self.list_types.append("ul")
        elif tag == "ol":
            self.list_numbers.append(0)
            self.list_types.append("ol")
        elif tag == "li":
            depth = max(len(self.list_numbers) - 1, 0)
            if self.list_types and self.list_types[-1] == "ol":
                self.list_numbers[-1] += 1
                marker = f"{self.list_numbers[-1]}."
            else:
                marker = "-"
            self.add("  " * depth + marker + " ")
        elif tag in {"th", "td"}:
            self.add("| ")
            self.in_table_header = tag == "th"
        self.stack.append((tag, attrs_dict))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "head"} and self.ignored_depth:
            self.ignored_depth -= 1
            return
        if self.ignored_depth:
            return
        if self.table_depth:
            self.table_html.append(f"</{tag}>")
            if tag == "table":
                self.table_depth -= 1
                if not self.table_depth:
                    self.ensure_newline(2)
                    self.add("".join(self.table_html))
                    self.table_html.clear()
                    self.ensure_newline(2)
            return
        if tag == "pre":
            self.in_pre = False
            self.ensure_newline(1)
            self.add("```")
            self.ensure_newline(2)
        elif tag in {"ul", "ol"}:
            if self.list_numbers:
                self.list_numbers.pop()
                self.list_types.pop()
            self.ensure_newline(2)
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "div", "section", "article", "blockquote", "li", "tr", "table", "figure"}:
            self.ensure_newline(2)
        elif tag == "a":
            href = ""
            for open_tag, attrs in reversed(self.stack):
                if open_tag == "a":
                    href = attrs.get("href", "")
                    break
            self.add(f"]({href})")
        elif tag in {"strong", "b"}:
            self.add("**")
        elif tag in {"em", "i"}:
            self.add("*")
        elif tag in {"del", "s", "strike"}:
            self.add("~~")
        elif tag == "code" and not self.in_pre:
            self.add("`")
        elif tag == "pre":
            self.in_pre = False
            self.ensure_newline(1)
            self.add("```")
        elif tag == "ul":
            if self.list_numbers:
                self.list_numbers.pop()
        elif tag == "ol":
            if self.list_numbers:
                self.list_numbers.pop()
            self.ordered_depth = max(self.ordered_depth - 1, 0)
        elif tag in {"th", "td"}:
            self.add(" ")
        if self.stack:
            for index in range(len(self.stack) - 1, -1, -1):
                if self.stack[index][0] == tag:
                    del self.stack[index:]
                    break

    def handle_data(self, data: str) -> None:
        if self.ignored_depth:
            return
        if self.table_depth:
            self.table_html.append(html.escape(data))
            return
        if self.in_pre:
            self.add(data)
        else:
            self.add(re.sub(r"\s+", " ", data))

    def convert(self) -> str:
        result = "".join(self.parts)
        return result.strip() + "\n"


def html_to_markdown(text: str) -> str:
    parser = HTMLToMarkdown()
    parser.feed(text)
    parser.close()
    return parser.convert()


def read_document(path: Path) -> str:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() in {".html", ".htm"}:
        return html_to_markdown(text)
    return text.replace("\r\n", "\n").replace("\r", "\n")


def title_for(content: str, source: Path) -> str:
    match = HEADING_RE.search(content)
    if match:
        title = re.sub(r"[*_`~]", "", match.group(1)).strip()
        if title:
            return title
    return source.stem.replace("_", " ").replace("-", " ").strip() or "Untitled"


def add_frontmatter(content: str, fields: dict[str, str]) -> str:
    match = FRONTMATTER_RE.match(content)
    if match:
        # Preserve user YAML byte-for-byte rather than guessing its syntax.
        return content
    lines = ["---"] + [f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in fields.items()] + ["---", ""]
    return "\n".join(lines) + content.lstrip("\n")


def split_url(value: str) -> tuple[str, str]:
    value = strip_wrapped_url(value)
    parsed = urlsplit(value)
    suffix = ""
    if parsed.fragment:
        suffix += "#" + parsed.fragment
    if parsed.query:
        suffix = "?" + parsed.query + suffix
    return parsed.path, suffix


def is_remote(value: str) -> bool:
    scheme = urlsplit(value).scheme.lower()
    return bool(scheme) or value.startswith("//")


def unique_attachment_path(attachments_root: Path, source: Path, reserved: dict[str, Path]) -> Path:
    base = clean_component(source.name)
    stem = Path(base).stem or "asset"
    suffix = Path(base).suffix
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    base = f"{stem}-{digest}{suffix}"
    index = 1
    candidate = base
    while (candidate.lower() in reserved and reserved[candidate.lower()].read_bytes() != source.read_bytes()) or (attachments_root / candidate).is_symlink() or ((attachments_root / candidate).exists() and (not (attachments_root / candidate).is_file() or (attachments_root / candidate).read_bytes() != source.read_bytes())):
        index += 1
        candidate = f"{stem}-{index}{suffix}"
    reserved[candidate.lower()] = source
    return Path(candidate)


def discover_documents(source: Path) -> tuple[Path, list[Path], tempfile.TemporaryDirectory[str] | None]:
    cleanup: tempfile.TemporaryDirectory[str] | None = None
    source = source.expanduser().resolve()
    if source.is_file() and source.suffix.lower() == ".zip":
        cleanup = tempfile.TemporaryDirectory(prefix="yuque-export-")
        extraction_root = Path(cleanup.name).resolve()
        with zipfile.ZipFile(source) as archive:
            for member in archive.infolist():
                member_path = (extraction_root / member.filename).resolve()
                try:
                    member_path.relative_to(extraction_root)
                except ValueError as error:
                    raise ValueError(f"unsafe ZIP member path: {member.filename}") from error
                if member.is_dir():
                    member_path.mkdir(parents=True, exist_ok=True)
                    continue
                member_path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source_handle, member_path.open("wb") as target_handle:
                    shutil.copyfileobj(source_handle, target_handle)
        root = Path(cleanup.name)
    elif source.is_file():
        root = source.parent
    elif source.is_dir():
        root = source
    else:
        raise FileNotFoundError(f"source does not exist: {source}")

    if source.is_file() and source.suffix.lower() in SUPPORTED_DOCUMENTS:
        files = [source]
    else:
        files = sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in SUPPORTED_DOCUMENTS and not any(part.startswith(".") for part in path.relative_to(root).parts))
    if not files:
        raise ValueError(f"no Markdown or HTML documents found in {source}")
    return root, files, cleanup


def plan_destination(source_root: Path, source_file: Path, root_folder: Path) -> Path:
    relative = source_file.relative_to(source_root)
    parts = list(relative.parts)
    parts[-1] = Path(parts[-1]).stem + ".md"
    return root_folder.joinpath(*(clean_component(part) for part in parts))


def plan_single_destination(root_folder: Path, output_name: str) -> Path:
    name = clean_component(Path(output_name).name)
    if not name.lower().endswith(".md"):
        name += ".md"
    return root_folder / name


def replace_reference(original: str, new_url: str) -> str:
    if original.startswith("<") and original.endswith(">"):
        return f"<{new_url}>"
    return new_url


def protect_code(content: str) -> tuple[str, dict[str, str]]:
    protected: dict[str, str] = {}

    def stash(match: re.Match[str]) -> str:
        token = f"\x00YUQUE_CODE_{len(protected)}\x00"
        protected[token] = match.group(0)
        return token

    content = FRONTMATTER_RE.sub(stash, content, count=1)
    content = re.sub(r"(?ms)^([`~]{3,})[^\n]*\n.*?^\1[^\n]*(?:\n|$)", stash, content)
    content = re.sub(r"`+[^`\n]*`+", stash, content)
    return content, protected


def checked_destination(vault: Path, relative: Path) -> Path:
    target = vault / relative
    if ".obsidian" in relative.parts:
        raise ValueError("migration must not write Obsidian configuration")
    if target.is_symlink():
        raise ValueError(f"destination is a symlink: {target}")
    try:
        target.resolve().relative_to(vault)
    except ValueError as error:
        raise ValueError(f"destination escapes vault: {target}") from error
    if target.exists() and not target.is_file():
        raise ValueError(f"destination is not a file: {target}")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Yuque Markdown/HTML file, export directory, or ZIP")
    parser.add_argument("vault", type=Path, help="Obsidian vault directory")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="plan without writing files (default)")
    mode.add_argument("--apply", action="store_true", help="write planned files after review")
    parser.add_argument("--overwrite", action="store_true", help="replace existing destination notes (requires --apply)")
    parser.add_argument("--root-folder", default="", help="relative folder inside the vault for migrated notes")
    parser.add_argument("--output-name", default="", help="exact destination basename for a single source document")
    parser.add_argument("--asset-root", type=Path, help="explicit attachment boundary for a single source file; defaults to its parent")
    parser.add_argument("--attachments-dir", default="attachments", help="relative vault folder for copied local assets")
    parser.add_argument("--source-url", default="", help="optional Yuque URL recorded in frontmatter")
    parser.add_argument("--no-frontmatter", action="store_true", help="do not add migration frontmatter")
    parser.add_argument("--report", type=Path, help="report path; defaults to VAULT/.yuque-migration-report.json")
    args = parser.parse_args()
    args.dry_run = not args.apply

    try:
        root_folder = safe_relative_path(args.root_folder) if args.root_folder else Path()
        attachments_dir = safe_relative_path(args.attachments_dir)
        source_root, source_files, cleanup = discover_documents(args.source)
        if args.output_name and len(source_files) != 1:
            raise ValueError("--output-name requires exactly one source document")
        if args.output_name and ("/" in args.output_name or "\\" in args.output_name or args.output_name in {".", ".."}):
            raise ValueError("--output-name is a basename; specify directories via --root-folder")
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    vault = args.vault.expanduser().resolve()
    asset_source_root = args.asset_root.expanduser().resolve() if args.asset_root else source_root
    try:
        source_arg = args.source.expanduser().resolve()
        if vault == source_root or source_root in vault.parents or vault in source_root.parents:
            raise ValueError("source and vault must not contain each other")
        if args.asset_root and (not asset_source_root.is_dir()):
            raise ValueError("--asset-root must be an existing directory")
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    attachment_root = vault / attachments_dir
    checked_destination(vault, attachments_dir / "__boundary_check__")
    destination_map: dict[Path, Path] = {}
    used_destinations: set[str] = set()
    documents: list[dict[str, object]] = []
    for source_file in source_files:
        destination = plan_single_destination(root_folder, args.output_name) if args.output_name else plan_destination(source_root, source_file, root_folder)
        key = destination.as_posix().lower()
        if key in used_destinations:
            stem, suffix = destination.stem, destination.suffix
            index = 2
            while destination.as_posix().lower() in used_destinations:
                destination = destination.with_name(f"{stem}-{index}{suffix}")
                index += 1
        used_destinations.add(destination.as_posix().lower())
        destination_map[source_file.resolve()] = destination
        destination_abs = checked_destination(vault, destination)
        documents.append({
            "source": source_file,
            "source_relative": source_file.relative_to(source_root).as_posix(),
            "destination": destination,
            "destination_abs": destination_abs,
            "existing": destination_abs.exists(),
            "action": "write" if args.overwrite or not destination_abs.exists() else "skip",
        })

    report: dict[str, object] = {
        "source": str(args.source.expanduser().resolve()),
        "vault": str(vault),
        "dry_run": args.dry_run,
        "documents": [],
        "attachments": [],
        "conflicts": [],
        "missing_assets": [],
        "remote_assets": [],
        "unresolved_links": [],
        "warnings": [],
    }
    asset_map: dict[Path, Path] = {}
    reserved_assets: dict[str, Path] = {}
    rendered: list[tuple[dict[str, object], str]] = []

    def record_unique(key: str, value: object) -> None:
        values = report[key]
        assert isinstance(values, list)
        if value not in values:
            values.append(value)

    def asset_url(source_file: Path, reference: str, destination: Path, is_image: bool) -> str:
        raw = strip_wrapped_url(reference)
        path_part, suffix = split_url(raw)
        if not path_part or is_remote(raw):
            if is_image and raw:
                record_unique("remote_assets", {"source": str(source_file), "url": raw})
            return reference
        candidate = (source_file.parent / unquote(path_part)).resolve()
        try:
            candidate.relative_to(asset_source_root.resolve())
        except ValueError:
            record_unique("missing_assets", {"source": str(source_file), "reference": raw, "reason": "outside export"})
            return reference
        if not candidate.is_file():
            record_unique("missing_assets", {"source": str(source_file), "reference": raw})
            return reference
        if candidate not in asset_map:
            target_name = unique_attachment_path(attachment_root, candidate, reserved_assets)
            asset_map[candidate] = attachments_dir / target_name
        target = asset_map[candidate]
        checked_destination(vault, target)
        relative_url = quote(posixpath.relpath(target.as_posix(), destination.parent.as_posix() or "."), safe="/._-~") + suffix
        record_unique("attachments", {"source": str(candidate), "destination": target.as_posix()})
        return replace_reference(reference, relative_url)

    for item in documents:
        source_file = item["source"]
        destination = item["destination"]
        assert isinstance(source_file, Path) and isinstance(destination, Path)
        if item["action"] == "skip":
            report["documents"].append({"source": item["source_relative"], "destination": destination.as_posix(), "existing": True, "action": "skip"})
            record_unique("conflicts", {"source": item["source_relative"], "destination": destination.as_posix()})
            continue
        content = read_document(source_file)
        content, protected = protect_code(content)
        if re.search(r"(?m)^\s*\[[^\]]+\]:\s*\S+|!\[\[[^\]]+\]\]", content):
            record_unique("warnings", {"source": item["source_relative"], "message": "Reference-style links or wikilinks require manual verification."})
        if source_file.suffix.lower() in {".html", ".htm"}:
            record_unique("warnings", {"source": item["source_relative"], "message": "HTML fallback: inspect lists, tables, formulas, and embedded content in Obsidian."})

        def rewrite_image(match: re.Match[str]) -> str:
            old = match.group(2)
            return match.group(0).replace(old, asset_url(source_file, old, destination, True), 1)

        content = ASSET_RE.sub(rewrite_image, content)

        def rewrite_html_image(match: re.Match[str]) -> str:
            old = match.group(2)
            return match.group(0).replace(old, asset_url(source_file, old, destination, True), 1)

        content = HTML_IMAGE_RE.sub(rewrite_html_image, content)

        def rewrite_doc_link(match: re.Match[str]) -> str:
            label, old = match.group(1), match.group(2)
            raw = strip_wrapped_url(old)
            path_part, suffix = split_url(raw)
            if not path_part or is_remote(raw) or path_part.startswith("#"):
                return match.group(0)
            candidate = (source_file.parent / unquote(path_part)).resolve()
            target = destination_map.get(candidate)
            if target is None and Path(path_part).suffix.lower() in SUPPORTED_DOCUMENTS:
                record_unique("unresolved_links", {"source": str(source_file), "reference": raw})
                return match.group(0)
            if target is None:
                new = asset_url(source_file, old, destination, False)
                return match.group(0).replace(old, new, 1)
            relative = quote(posixpath.relpath(target.as_posix(), destination.parent.as_posix() or "."), safe="/._-~") + suffix
            return match.group(0).replace(old, replace_reference(old, relative), 1)

        content = LINK_RE.sub(rewrite_doc_link, content)
        for token, original in protected.items():
            content = content.replace(token, original)
        if not args.no_frontmatter:
            fields = {"title": title_for(content, source_file), "migrated_from": item["source_relative"]}
            if args.source_url:
                fields["source_url"] = args.source_url
            content = add_frontmatter(content.rstrip() + "\n", fields)
        rendered.append((item, content))
        report["documents"].append({
            "source": item["source_relative"],
            "destination": destination.as_posix(),
            "existing": item["existing"],
            "action": item["action"],
        })
        if item["existing"] and not args.overwrite:
            record_unique("conflicts", {"source": item["source_relative"], "destination": destination.as_posix()})

    if not args.dry_run:
        vault.mkdir(parents=True, exist_ok=True)
        for candidate, target in asset_map.items():
            target_abs = checked_destination(vault, target)
            target_abs.parent.mkdir(parents=True, exist_ok=True)
            if not target_abs.exists():
                shutil.copy2(candidate, target_abs)
        for item, content in rendered:
            if item["action"] != "write":
                continue
            destination_abs = item["destination_abs"]
            assert isinstance(destination_abs, Path)
            destination_abs.parent.mkdir(parents=True, exist_ok=True)
            destination_abs.write_text(content, encoding="utf-8")
        report["written_documents"] = sum(1 for item in documents if item["action"] == "write")
        report["skipped_documents"] = sum(1 for item in documents if item["action"] == "skip")
        report_path = args.report.expanduser() if args.report else vault / ".yuque-migration-report.json"
        if not report_path.is_absolute():
            report_path = vault / report_path
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        report["written_documents"] = 0
        report["skipped_documents"] = sum(1 for item in documents if item["action"] == "skip")

    print(json.dumps({
        "dry_run": args.dry_run,
        "documents": len(documents),
        "to_write": sum(1 for item in documents if item["action"] == "write"),
        "skipped": report["skipped_documents"],
        "attachments": len(report["attachments"]),
        "conflicts": len(report["conflicts"]),
        "missing_assets": len(report["missing_assets"]),
        "remote_assets": len(report["remote_assets"]),
        "unresolved_links": len(report["unresolved_links"]),
        "warnings": len(report["warnings"]),
        "plan": report["documents"],
        "issues": {key: report[key] for key in ("conflicts", "missing_assets", "remote_assets", "unresolved_links", "warnings")},
    }, ensure_ascii=False, indent=2))
    if cleanup is not None:
        cleanup.cleanup()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, UnicodeError, zipfile.BadZipFile) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
