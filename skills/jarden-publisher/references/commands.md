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

国内平台的 `settings` 统一写 `visibility`（`public`、`private`、`friends` 或 `followers`）、`scheduled`、`ai_generated` 和核对到的封面 `cover`。小红书用 `first_image`，再写 `original`、`allow_copy`。抖音用 `first_image` 或 `custom_image`，再写 `allow_save`、`cross_posting`。B 站用 `text_preview` 或 `custom_image`，再写 `original`、`comments_enabled`、`featured_comments`。开关按页面实况写布尔值。`custom_image` 另写 `cover_source_sha256`，记录与用户已确认的封面源图相匹配的摘要。

海外 `readback` 使用 `mode`、`caption`、`topics`、`image_sha256` 和 `settings`。TikTok 加 `title`，`mode` 写 `photo-post`。Instagram 的 `mode` 写 `feed-carousel`，`caption` 覆盖正文和标题检查。`topics` 按完整文案的顺序记实际标签，包括正文自带标签，具体清单见 `preflight.py`。

海外 `settings` 写 `visibility`、`scheduled`、`cover`、`ai_generated`、`comments_enabled`、`branded_content` 和 `cross_posting`。TikTok 将 Only you 记为 `private`，Instagram 私密账号的一般帖子记为 `followers`，面向亲密好友的帖子记为 `close_friends`。核对当前生效范围和即时或定时提交。最后四项使用观察到的布尔值，入口确无对应功能时记 `not_available` 并在摘要中说明。`cover` 通常为 `first_image`。TikTok 若有已确认的其他轮播图作为封面，写 `selected_image` 和对应的 `cover_source_sha256`。字段未核实、所需标识缺失或页面并非图文模式时，保留 `editing` 或 `blocked`。

```sh
python3 "$SKILL_DIR/scripts/job.py" observe "$JOB_DIR" "$PLATFORM" --file "$EVIDENCE"
```

## 记录提交

给用户核对稿件、账号、受众和设置。海外两站同时留存这份 `ready` 记录的 `review_id`，供本次授权使用。版本、账号或设置变化时重做审阅并取得新的批准。收到明确提交指令后，再读一次同一编辑页，将晚于上次就绪记录的证据和用户原话交给 `authorize`。用户告知已经发布时走回查。程序返回 `publish_unknown` 后可执行站点允许的唯一提交动作。定时、同步发布或无法核定唯一发布目的地时由用户操作。结果不明时只查回执。

```sh
python3 "$SKILL_DIR/scripts/job.py" authorize "$JOB_DIR" "$PLATFORM" \
  --file "$EVIDENCE" --instruction "$USER_INSTRUCTION"
```

海外两站在上述命令追加 `--review-id "$REVIEW_ID"`。使用给用户看过的版本所对应的 ID。较早的海外记录可以重新回读生成此 ID。

平台接受时用 `observe` 记录 `confirmed`，并用 `publication` 区分提交、审核和已发布。国内平台按实际作品 ID 或链接核对。TikTok 与 Instagram 使用实际图文链接，TikTok 核对 `/@用户/photo/数字ID`，Instagram 核对 `/p/短码/`，短码区分大小写。回执尚未出现时沿用 `publish_unknown`，只查原平台的作品管理。程序负责本地字段、文件、时序和状态校验。浏览器的观察者负责核验实际页面。

## 回查手机发布

用户自行发布后，从实际图文页回读作者、图序和完整文案。对 TikTok 或 Instagram 使用 `reconcile` 把结果写回原任务。证据填写 `status: confirmed`、`account`、`url`、`observed_at`、`summary`、`publication` 和 `fingerprint`。当 `url` 是管理页时，另填图文作品的 `work_url`。`readback` 使用该平台的图片、模式与文案字段。发布页可核对到的范围或设置写入摘要。已有记录时，账号和已知作品 ID 须一致。仅有本地内容包也可回查。

```sh
python3 "$SKILL_DIR/scripts/job.py" reconcile "$JOB_DIR" "$PLATFORM" --file "$EVIDENCE"
```

## 本地回归

```sh
python3 -B -m unittest discover -s "$SKILL_DIR/tests" -v
```
