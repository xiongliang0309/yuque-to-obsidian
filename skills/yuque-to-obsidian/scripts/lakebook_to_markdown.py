#!/usr/bin/env python3
"""Stage documents from an authenticated Yuque Lakebook export."""

import argparse
import importlib.util
import json
import tarfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("lakebook", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--base-url", help="override the source URL recorded in Lakebook metadata")
    parser.add_argument("--slug", action="append", default=[], help="only stage this document slug; repeat to select multiple")
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("yuque_converter", Path(__file__).with_name("yuque_to_obsidian.py"))
    converter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(converter)
    if args.destination.exists():
        if not args.destination.is_dir():
            raise ValueError("Staging destination is not a directory")
        if any(args.destination.iterdir()):
            raise FileExistsError("Staging destination must be empty")
    with tarfile.open(args.lakebook, "r:*") as archive:
        metas = [m for m in archive.getmembers() if m.isfile() and m.name.endswith("/$meta.json")]
        if len(metas) != 1:
            raise ValueError("Expected one Lakebook metadata file")
        prefix = metas[0].name.rsplit("/", 1)[0]
        unexpected = [m.name for m in archive.getmembers() if m.isfile()
                      and not (m.name.startswith(prefix + "/") and m.name.endswith(".json"))]
        if unexpected:
            raise ValueError("Lakebook has non-JSON assets; inspect and stage attachments separately: "
                             + repr(unexpected[:10]))
        meta = json.loads(json.load(archive.extractfile(metas[0]))["meta"])
        base_url = args.base_url or meta.get("book", {}).get("path", "")
        if not base_url.startswith("https://www.yuque.com/"):
            raise ValueError("Missing Yuque book URL; provide --base-url")
        docs = meta["docs"]
        available_slugs = {doc["slug"] for doc in docs}
        if set(args.slug) - available_slugs:
            raise ValueError("Document slug not in Lakebook: " + repr(set(args.slug) - available_slugs))
        planned = []
        used_names = set()
        for doc in docs:
            slug = doc["slug"]
            if args.slug and slug not in args.slug:
                continue
            if slug in {"", ".", ".."} or "/" in slug or "\\" in slug:
                raise ValueError("Unsafe document slug: " + slug)
            member = archive.getmember(prefix + "/" + slug + ".json")
            if not member.isfile():
                raise ValueError("Document is not a regular archive file: " + slug)
            raw = json.load(archive.extractfile(member))["doc"]
            if raw["slug"] != slug or raw["title"] != doc["title"]:
                raise ValueError("Document metadata mismatch: " + slug)
            if raw.get("format") not in {None, "", "lake", "html"}:
                raise ValueError("Unsupported document body format: " + str(raw.get("format")))
            stem = converter.clean_component(doc["title"])
            name = stem + ".md"
            suffix = 2
            while name.casefold() in used_names:
                name = f"{stem}-{suffix}.md"
                suffix += 1
            used_names.add(name.casefold())
            output = args.destination / name
            if output.exists():
                raise FileExistsError(output)
            body = raw.get("body") or raw.get("body_draft") or ""
            if not body and (raw.get("body_asl") or raw.get("body_draft_asl")):
                raise ValueError("Document has editor content but no HTML body: " + slug)
            markdown = converter.html_to_markdown(body) if body else ""
            fields = {"title": doc["title"], "source_url": base_url.rstrip("/") + "/" + slug, "migrated_from": args.lakebook.stem + "/" + doc["title"]}
            planned.append((output, converter.add_frontmatter(markdown, fields), {
                "source": fields["source_url"], "target": str(output),
                "status": raw.get("status", doc.get("status")), "body_chars": len(markdown),
                "content_source": "body" if raw.get("body") else "body_draft" if body else "empty",
                "draft_differs": bool(raw.get("body") and raw.get("body_draft")
                                      and raw["body"] != raw["body_draft"]),
            }))
    args.destination.mkdir(parents=True, exist_ok=True)
    for output, content, _ in planned:
        output.write_text(content, encoding="utf-8")
    print(json.dumps([item[2] for item in planned], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
