---
name: yuque-to-obsidian
description: "将指定语雀文档或知识库迁移到指定的 Obsidian 笔记库位置。默认用 Codex 内置浏览器打开语雀链接并复用其中的登录会话，支持 .lakebook、Markdown、HTML、ZIP 导出，保留链接、附件和迁移诊断。适用于授权迁移，不用于通用 Markdown 整理或未授权抓取。"
---

# 语雀迁移到 Obsidian

当用户需要将指定语雀文档或知识库迁移到 Obsidian 笔记库中的指定位置时，使用此技能。

## 执行原则

- 默认使用 Codex 内置浏览器打开用户提供的语雀链接，复用内置浏览器中已有的语雀标签页和登录会话。不因 Chrome、Edge 或 Safari 已登录就自动切换过去；只有用户明确指定其他浏览器时才按其选择执行。明确源文档或知识库页面，以及目标笔记库、目标目录或文件名；只针对缺失信息提问。
- 浏览器访问受授权范围限制：复用可见的登录会话，不索取或保存账号凭据，不绕过登录、反爬机制、付费限制或访问权限。会话不可用时，请用户提供本地 .lakebook、Markdown、HTML 或 ZIP 导出文件。
- 区分知识库与单篇文档：先在可见界面核对文档清单、数量及草稿状态；单篇请求不得擅自扩大到整库，空白文档不伪称有正文。
- 写入前展示准确映射：语雀源文档 -> Obsidian 目标文件。目标为目录时保留源文件名；目标为具体文件名时只迁移单篇文档。不得将单篇迁移擅自扩大为整个知识库。
- 先执行预演（`--dry-run`），展示映射、文档数、附件数、冲突及未解析或远程链接。用户明确指定源和目标的迁移请求已授权在该位置新建文件，无需重复确认；覆盖已有笔记或改变迁移范围前必须确认。
- 不删除或重命名笔记库中无关的内容。未获明确覆盖授权时，将已有目标文件视为冲突；获授权后才使用 `--overwrite`。
- 原样保留已有 YAML 属性区（frontmatter）；仅在不存在属性区时添加最少的迁移字段。保留中文文本和 Unicode 文件名，只处理不安全的文件名字符。
- 保留远程图片和链接，并在报告中记录；将本地附件复制到配置的附件目录，重写为相对路径引用。
- 写入后检查 JSON 报告，汇总冲突、跳过文件、未解析链接及远程资源。脚本执行成功不代表所有外部资源都已下载。

## 浏览器衔接

通过可用的浏览器控制工具操作 Codex 内置浏览器（`iab`），并遵循工具返回的文档。已有内置语雀标签页时复用；否则在可见的内置浏览器标签页打开源链接。未登录或需要验证码时，请用户在该标签页手动完成；用户确认后检查页面登录状态再继续。不要默认要求安装外部浏览器扩展或切换浏览器。工具不可用时说明限制，并请求本地导出或由用户明确选择其他浏览器；不得读取浏览器配置、Cookie 或令牌来绕过限制。具体入口及导出路线见[浏览器获取流程](references/browser-workflow.md)。

## 迁移流程

1. 阅读[迁移参考](references/migration.md)，了解目标映射和输出行为。使用浏览器时，还需阅读[浏览器获取流程](references/browser-workflow.md)。
2. 在内置浏览器打开源链接并核实登录状态，检查指定范围。整库优先通过语雀界面导出“语雀知识库 .lakebook”，单篇优先原生 Markdown；核实文档数、标题、状态，排除无关导航和评论。
3. 将源材料保存在临时工作目录。整库 .lakebook 是 tar 容器而非 ZIP；先检查内容，再用 `scripts/lakebook_to_markdown.py BOOK.lakebook EMPTY_STAGING_DIR` 暂存 Markdown。只迁移部分文档时重复指定 `--slug SLUG`。暂存目录须为空，且不得位于笔记库内。其他 Markdown、HTML、ZIP 可直接交给转换器。
4. 执行 `scripts/yuque_to_obsidian.py SOURCE VAULT --root-folder TARGET_FOLDER --dry-run` 并展示计划；SOURCE 为暂存目录或本地 Markdown/HTML/ZIP，不是 .lakebook。核对逐篇映射、空白文档、附件、冲突和未解析/远程链接；单篇目标文件名才使用 `--output-name NAME.md`。经确认的附件在单篇文档父目录之外时用 `--asset-root EXPORT_ROOT` 指定导出根目录。
5. 已获目标位置授权后，以 `--apply` 执行写入；只有明确获准覆盖该目标文件时才添加 `--overwrite`。用户的迁移意图不能替代文件系统沙箱审批。
6. 检查 JSON 报告，逐篇比对源正文与目标文件的标题、首尾及关键内容；抽查附件、链接及目录。若 Obsidian 应用内未能打开笔记，只说明已完成磁盘文件核验。

## 脚本用法

脚本只依赖 Python 标准库，不需要联网。以下大写项为路径或文件名占位符，执行时替换为实际值：

```bash
python3 scripts/yuque_to_obsidian.py EXPORT_PATH OBSIDIAN_VAULT --dry-run
python3 scripts/yuque_to_obsidian.py EXPORT_PATH OBSIDIAN_VAULT --root-folder TARGET_FOLDER --output-name NOTE.md --apply
python3 scripts/lakebook_to_markdown.py BOOK.lakebook EMPTY_STAGING_DIR
python3 scripts/yuque_to_obsidian.py EMPTY_STAGING_DIR OBSIDIAN_VAULT --root-folder BOOK_NAME --dry-run
python3 scripts/yuque_to_obsidian.py EMPTY_STAGING_DIR OBSIDIAN_VAULT --root-folder BOOK_NAME --apply --report REPORT.json
```

上述大写名称均为占位符。通过 `--help` 查看转换器的 `--root-folder`、`--output-name`（仅限单篇）、`--asset-root`、`--attachments-dir`、`--source-url`、`--no-frontmatter` 和 `--report`。Lakebook 暂存器支持重复 `--slug` 选择文档；输出状态和正文长度，但不直接写入笔记库。远程 URL 和引用式链接不会自动下载或改写，须按报告人工复核。修改脚本后运行 `python3 scripts/test_yuque_to_obsidian.py` 和 `python3 scripts/test_lakebook_to_markdown.py`。
