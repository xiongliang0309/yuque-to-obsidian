# 语雀迁移到 Obsidian（Codex Skill）

将指定的语雀文档或知识库导出为 Markdown 笔记，迁移到指定的 Obsidian 笔记库目录。默认使用 Codex 内置浏览器打开用户提供的语雀链接；用户在浏览器中自行登录。迁移前显示源文档到目标文件的映射并预演，只有指定位置的新文件才会写入；已有文件不会在未经授权时覆盖。

## 安装

在支持 Skills 的 Codex 中说：

> 使用 skill-installer，从 `https://github.com/xiongliang0309/yuque-to-obsidian/tree/main/skills/yuque-to-obsidian` 安装技能。

或者将仓库的 `skills/yuque-to-obsidian` **整个目录**放进 `~/.codex/skills/`（如设置了 `CODEX_HOME`，则放进其 `skills/` 目录）。不要只复制 `SKILL.md`：转换脚本和参考流程也在该目录中。

需要 Python 3；转换脚本只依赖 Python 标准库。网页迁移还需要 Codex 环境提供可用的内置浏览器控制功能。本技能不索取或保存语雀凭据；浏览器和本地文件写入权限需由使用者授予。

## 使用

> 使用 $yuque-to-obsidian，在内置浏览器打开 https://www.yuque.com/…，把指定的“知识库名”迁移到我的 Obsidian 笔记库“目标目录”。先预演，再写入并核对报告。

如未登录，先在内置标签页自行登录，随后告诉 Codex 继续。提供明确的源文档或知识库以及目标笔记库路径；单篇文档与整库是不同范围。也支持由用户提供本地 `.lakebook`、Markdown、HTML 或 ZIP 导出，使用时仍应明确目标位置。

## 工作方式与限制

- 整库优先用语雀页面提供的 `.lakebook` 导出；这是 tar 容器，不是 ZIP。技能先从中暂存 Markdown，再生成预演报告，最后按授权写入 Obsidian。
- 远程图片及文件链接默认保留原 URL，不能保证离线可用。归档内出现未处理附件时会停止，而不是静默丢弃。
- 当前的 Lakebook 暂存器将文档平铺到指定目录，不自动还原语雀目录树。复杂公式、表格、嵌入卡片等应对照原文人工核验；空白草稿会被如实标记。
- 如果目标文件已存在，会报告冲突并跳过；只有获得明确覆盖授权才可覆盖。迁移不会删除原语雀内容或笔记库中无关文件。
- 不要把私人语雀导出、笔记库、迁移报告、Cookie 或令牌加入公开仓库。

## 本地验证

```bash
python3 skills/yuque-to-obsidian/scripts/test_yuque_to_obsidian.py
python3 skills/yuque-to-obsidian/scripts/test_lakebook_to_markdown.py
```

这两组测试使用合成样例，不含真实语雀文档。完整执行细节见 [SKILL.md](skills/yuque-to-obsidian/SKILL.md)。

## 许可

目前未附开源许可证；公开可见不等于授予他人复制、修改或再分发权限。正式推广前，仓库所有者应选定许可证并添加 `LICENSE` 文件。
