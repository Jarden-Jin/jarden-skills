# 命令

`SKILL_DIR` 为本 Skill 目录。`JOB_DIR` 为本次内容包所在的本地任务目录。账号、浏览器空间与标签都取自当次真实页面。恢复时读取原任务和原页面。

```sh
python3 "$SKILL_DIR/scripts/package.py" check "$JOB_DIR"
python3 "$SKILL_DIR/scripts/job.py" status "$JOB_DIR"
```

## 发起传图

小红书使用 Ego Lite 且已打开空白图文上传页时，可以用下列入口。它核对当前账户、图文页签、图片控件和源文件字节，登记传图任务。在同一编辑页出现后，填写并回读内容包内的标题和正文。用 `media_selected` 和 `copy_filled` 区分进度，再核对图片、话题、封面和关键选项。

```sh
python3 "$SKILL_DIR/scripts/xiaohongshu-upload.py" "$JOB_DIR" \
  --space "$SPACE" --tab "$TAB" --account "$ACCOUNT"
```

使用其他受控浏览器或其他平台时，按同一原则先登记再操作。

```sh
python3 "$SKILL_DIR/scripts/job.py" begin "$JOB_DIR" "$PLATFORM" \
  --space "$SPACE" --tab "$TAB" --account "$ACCOUNT" --url "$CREATOR_URL"
```

## 保存回读

获得平台现场证据后才构造证据文件。`status` 取 `editing`、`blocked`、`ready` 或 `confirmed`。填写 `space`、`tab`、`account`、`url`、带时区的 `observed_at`、`summary` 和当前 `fingerprint`。在 `summary` 中说明读到的控件、图片映射、阻塞和已经完成的动作。手头有截图或只读回证文件时记录入口。

对于 `ready`，`checks` 记录七项页面回读：`mode`、`images`、`title`、`body`、`topics`、`settings`、`final_control`。`durable_draft` 仅在草稿可稳定回开且有平台 `draft_id` 时为真；仅恢复最近暂存稿可另外记录 `saved_entry` 和 `saved_fields`。`readback` 记录实际 `title`、`body`、`topics`、按可见顺序映射的 `image_sha256`、`settings`，以及专栏的 `placements`。预览被平台转码时，逐图核对画面与顺序，另外记录预览证据。

`settings` 统一写 `visibility`（`public`、`private`、`friends` 或 `followers`）、`scheduled`、`ai_generated` 和核对到的封面 `cover`。小红书用 `first_image`，再写 `original`、`allow_copy`。抖音用 `first_image` 或 `custom_image`，再写 `allow_save`、`cross_posting`。B 站用 `text_preview` 或 `custom_image`，再写 `original`、`comments_enabled`、`featured_comments`。开关按页面实况写布尔值。`custom_image` 另写 `cover_source_sha256`，记录与用户已确认的封面源图相匹配的摘要。字段未核实就保留 `editing` 或 `blocked`；旧记录无需改写，继续提交前须重新回读。

```sh
python3 "$SKILL_DIR/scripts/job.py" observe "$JOB_DIR" "$PLATFORM" --file "$EVIDENCE"
```

## 记录提交

用户明确批准本次发布到指定平台后，再读一次同一编辑页。将晚于上次就绪记录的观察证据直接交给 `authorize`，并使用当次原话，例如「确认发布这篇到小红书」。发布范围应已给用户核对；如果版本或设置变化，先更新就绪记录并请用户确认。程序登记一次尝试；返回 `publish_unknown` 后才可执行站点允许的唯一提交动作。定时或同步发布由用户操作。结果不明时只查回执。

```sh
python3 "$SKILL_DIR/scripts/job.py" authorize "$JOB_DIR" "$PLATFORM" \
  --file "$EVIDENCE" --instruction "$USER_INSTRUCTION"
```

平台接受时用 `observe` 记录 `confirmed`，包含实际 `work_id` 或 `work_url`，并用 `publication` 区分提交、审核和已公开。回执尚未出现时沿用 `publish_unknown`，只查原平台的作品管理。程序负责本地字段、文件、时序和状态校验。浏览器的观察者负责核验实际页面。

## 本地回归

```sh
python3 -B -m unittest discover -s "$SKILL_DIR/tests" -v
```
