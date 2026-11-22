# 内容包

本地工具检查输出文件的 SHA-256、真实图片格式与解码、图序和必要字段。`post/` 导入适用于已导出的 schema 1。兼容旧版只有 `files` 中成品摘要的清单。`caption.md` 与清单里的标题、正文及话题须一致。图片取 `images` 的顺序和图位。

```sh
python3 "$SKILL_DIR/scripts/package.py" build --post "$POST_DIR" --out "$JOB_DIR"
python3 "$SKILL_DIR/scripts/package.py" build \
  --image "$FIRST_IMAGE" --image "$NEXT_IMAGE" \
  --title "$TITLE" --body-file "$BODY" --topic "角色设计" --out "$JOB_DIR"
python3 "$SKILL_DIR/scripts/package.py" check "$JOB_DIR"
```

`--dry-run` 只检查和预览字段。可重复传 `--platform xiaohongshu`、`--platform douyin`、`--platform bilibili`、`--platform tiktok` 或 `--platform instagram`。`blockers` 按平台列出缺项或超限。用户指定的标题、正文和话题分别用 `--title`、`--body-file` 和重复的 `--topic` 覆盖。话题源可以含 `#` 前缀，存储按原顺序去掉前缀，实际选中依据平台回读。如果平台要求不同字段，先核对必要差异并为该平台单独冻结内容包。

本地预检按小红书最多 18 图、抖音最多 35 图和 5 个话题执行。到编辑页再核对账号实际限制。超限时保留用户提供的素材与话题原样，确认取舍后再建包。

旧包正文中的明确 AI 效果图声明会带入发布设置。文案覆盖只改变展示文字。已知 AI 来源也可用 `--ai-generated` 登记。`--original` 用于有权利依据的国内平台原创设置。用平台原生标识处理 AI 来源并保留图像字节和现有标识。公开文案沿用确认的内容，事实描述以素材依据为准。

## 海外图文

- **TikTok Photo Post** 的 `caption` 是正文加已选的 `#hashtags`，填到描述栏。计划有独立标题时，先检查当前编辑器能否填写。缺少该栏时，由用户确认标题并入描述或省略，再冻结新版本。本地预检最多 35 张图。描述多于 5 个标签时提前核对当次账号上限，实际图片规格和字数按当前编辑器核对。
- **Instagram Feed Carousel** 使用一份 `caption`。已有标题作为首段，接正文和 `#hashtags`，首图对应轮播封面。本地预检 2 至 20 张图、最多 5 个 `#` 标签。查看实际账号选择上限、格式、统一版式和逐图预览。参见 [Instagram 标签说明](https://help.instagram.com/351460621611097)。

预检中 `caption` 就是实际要填写的完整文字。两站均可省略未选的话题，正文已含的同名标签保留一份。标题或文案刻意留空时在预检中说明。发布语言或标签需调整时先核对并冻结新版本。图片尺寸、格式或正文超限时，保留原件并由用户确认取舍、裁切或转换，之后重建内容包。

## B 站专栏

给 `--platform bilibili --article-file "$ARTICLE"`。读取真实正文并原样保留 UTF-8 文本，以空行为段落边界。看图和正文，按实际语义挑图位。`--placements-file` 指向 JSON 数组，每个对象包含 1 起计的 `image` 与 `after`，`after: 0` 放在正文前。每张图出现一次，工具按 `after` 排列，同一段的图保持计划顺序。

```json
[
  {"image": 1, "after": 0},
  {"image": 2, "after": 3}
]
```

标题、正文、插图和话题分别核验。保留段落文本与顺序。对于独立的封面控件，使用用户给定的封面或已选首图，并在回读时区分封面与正文图片。
