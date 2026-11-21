# 内容包

本地工具检查输出文件的 SHA-256、真实图片格式与解码、图序和必要字段。`post/` 导入适用于已导出的 schema 1。兼容旧版只有 `files` 中成品摘要的清单。`caption.md` 与清单里的标题、正文及话题须一致。图片取 `images` 的顺序和图位。

```sh
python3 "$SKILL_DIR/scripts/package.py" build --post "$POST_DIR" --out "$JOB_DIR"
python3 "$SKILL_DIR/scripts/package.py" build \
  --image "$FIRST_IMAGE" --image "$NEXT_IMAGE" \
  --title "$TITLE" --body-file "$BODY" --topic "角色设计" --out "$JOB_DIR"
python3 "$SKILL_DIR/scripts/package.py" check "$JOB_DIR"
```

`--dry-run` 只检查和预览字段。可重复传 `--platform xiaohongshu`、`--platform douyin`、`--platform bilibili`。`blockers` 按平台列出缺项或超限。用户指定的标题、正文和话题分别用 `--title`、`--body-file` 和重复的 `--topic` 覆盖。话题源可以含 `#` 前缀，存储按原顺序去掉前缀，实际选中依据平台回读。如果平台要求不同字段，先核对必要差异并为该平台单独冻结内容包。

本地预检按小红书最多 18 图、抖音最多 35 图和 5 个话题执行。到编辑页再核对账号实际限制。超限时保留用户提供的素材与话题原样，确认取舍后再建包。

旧包正文中的明确 AI 效果图声明会带入发布设置。文案覆盖只改变展示文字。已知 AI 来源也可用 `--ai-generated` 登记。`--original` 只在有明确原创权利依据时使用。用平台的原生标识表达已知 AI 来源，并保留图像字节和现有标识。正文尊重作者的取舍，事实描述以素材依据为准。

## B 站专栏

给 `--platform bilibili --article-file "$ARTICLE"`。读取真实正文并原样保留 UTF-8 文本，以空行为段落边界。看图和正文，按实际语义挑图位。`--placements-file` 指向 JSON 数组，每个对象包含 1 起计的 `image` 与 `after`，`after: 0` 放在正文前。每张图出现一次，工具按 `after` 排列，同一段的图保持计划顺序。

```json
[
  {"image": 1, "after": 0},
  {"image": 2, "after": 3}
]
```

标题、正文、插图和话题分别核验。保留段落文本与顺序。对于独立的封面控件，使用用户给定的封面或已选首图，并在回读时区分封面与正文图片。
