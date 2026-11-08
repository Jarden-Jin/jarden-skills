# 项目状态、交付与恢复

项目状态是 `project.json`；HTML、浏览器草稿和导出的反馈都不是批准状态。JSON 使用 UTF-8，命令返回 JSON。`<项目>` 是目录参数，路径有空格时作为单个参数传入。

## 版本与事务

- schema 2 的 `revision` 是业务版本，成功写入选择、答案或定稿后递增。
- `requirements_revision` 只在产品形态、主体、数量、笔记或交付方式变化时递增，用于让旧轮次失效。
- `present_round.py` 创建交付、写索引和记录打开结果时，不改变业务 revision。
- 所有写入先在临时文件写完并同步，再通过项目锁和事务日志替换；中断后下一次读取会收敛到完整状态。并发提交后到者得到 `stale_revision`，不会覆盖先到结果。
- `decisions` 记录保持项，`origin` 只能是 `user-confirmed`、`agent-inferred` 或 `reference-derived`。Agent 的推断不能自动升级成用户确认。

## 项目结构

```text
<项目>/
  project.json
  inputs/
  rounds/<轮次>/round.json
  rounds/<轮次>/A.png
  artifacts/sha256/<摘要>.<扩展名>
  deliveries/D0001/
    manifest.json
    index.html
    receipts.json
  decisions/<request_id>.json
  reviews/physics/
  reviews/creative/ 等分类报告
  copy.json
  gallery.html
  post/
```

`deliveries` 是不可变交付索引。manifest 记录当时的项目版本、图位、图片哈希、真实上次交付和比较基线；历史图片复制到内容寻址路径，原轮次图片被替换后旧交付仍可查看。`receipts.json` 只记录打开或提交回执，不伪造页面已被用户看见。

## 建立、查看和保存答案

```text
<python> <skill>/scripts/project.py init <项目> --input <图> --kind wireframe
<python> <skill>/scripts/project.py show <项目>
<python> <skill>/scripts/project.py set <项目> answers.product_form=plush answers.subject_scope=single
<python> <skill>/scripts/project.py set <项目> 'answers.subjects=["角色名"]' answers.note_type=single-multiview
<python> <skill>/scripts/project.py set <项目> answers.concept_count=1 answers.delivery=full-post image_backend.kind=builtin
```

输入类型：wireframe、sketch、flat-design、ai-image、photo、text、reference。重复 `init` 只读已有项目，不清空。answers 存确认过的回答；brief 存角色、卖点、补充、bible 和 decisions；preferences.directions_per_round 存每轮方向数，默认 3；image_backend 存能力和普通设置，不存 Key。

修改品类、角色或概念数量会失效后续定稿；改笔记类型和交付形式会失效场景与发帖。较早阶段没完成时仍留在较早阶段。旧文件保留，不再作为当前定稿。角色名必须唯一；缩减名单时同步数量并说明保留哪些角色。

## 旧项目迁移

schema 1 项目只读展示，不能自动写入。先预览：

```text
<python> <skill>/scripts/migrate_project.py <项目> --out <项目外计划.json>
<python> <skill>/scripts/migrate_project.py <项目> --apply --plan <项目外计划.json>
```

预览前后项目字节不变。执行前核对 `project_sha256`；项目或计划变了就拒绝。迁移保留未知字段、输入、轮次、选择、审查和 post，备份原 `project.json` 到 `backups/migrations/`，重复执行返回 unchanged。迁移不会重做图片，也不会把旧审查变成通过。未来 schema 明确报错，不猜字段或降级覆盖。

## 开轮和登记

```text
<python> <skill>/scripts/project.py new-round <项目> --stage concept --subject <角色> --count 3
<python> <skill>/scripts/project.py accept-input <项目> --stage concept --subject <角色> --path inputs/design.png
<python> <skill>/scripts/project.py record-round <项目> <轮次>
```

round.json 每项有 id、title、idea、prompt、image、method；生成结果写到指定路径。`refs.source` 是身份或产品参考，`refs.style` 是系列或已选风格，`refs.based_on` 是沿用方向；路径相对项目根。method=generate 的 prompt 至少 40 字，compose/derive 要写 sources。

scene 轮在 record-round 前必须完成逐张零上下文物理审查，所有方法都适用；缺审、未通过、不确定或换图后旧哈希都不能继续。手动 `final` 也不能绕过。缺图可 `--allow-missing` 登记展示，但缺图、透明不合格和物理失败不能 select 或推进。

## 选择、页面提交和回改

```text
<python> <skill>/scripts/project.py select <项目> <轮次> A
<python> <skill>/scripts/project.py select <项目> <轮次> --approve --skip scene-2
<python> <skill>/scripts/project.py new-round <项目> --stage material --based-on <轮次>:B
<python> <skill>/scripts/project.py feedback <项目> --file feedback.json
<python> <skill>/scripts/project.py resolve-intent <项目> --request-id <ID> --status completed
```

alternatives 是同一决策的互斥候选，set 是一组不同用途的图。概念和材质用 alternatives，透明资产用 set。只能选当前需求下已登记、实际存在且通过必要检查的图片；superseded 不能重新激活。

真人明确选定时 `select` 默认记录 `origin=user-confirmed`。获得授权的模拟客户或 Agent 推断必须加 `--origin agent-inferred`；来自参考材料而非真人选定时用 `reference-derived`。页面提交默认记真人动作，自动测试的 request 可显式带同名 `origin`。未知来源的旧选择只作为参考，不自动冒称用户批准。

页面通过 `serve_gallery.py` 的受控接口提交，核心调用 `submit`：

```text
<python> <skill>/scripts/project.py submit <项目> --request request.json
```

request 至少包含 schema_version、request_id、project_id、delivery_id、expected_revision、round_id、action、items、note 和 keep。action 为 answer-field、select、approve-set、request-variation、request-slots 或 request-redo。answer-field 只答当前唯一问题；补图和重做只登记意向，不自动调用生图。相同 request_id 和内容返回同一回执，不同内容拒绝；旧 revision、旧交付、换图、伪造图位和重复图位都拒绝。

feedback 记录用户原话、Agent 解释、可观察改法、保持项、影响范围、比较基线；不自动把模糊意见解释成换角色或撤销全部确认。项目里没有有效审查的图不改写成通过。

## 交付与发帖

```text
<python> <skill>/scripts/present_round.py <项目> --no-open
<python> <skill>/scripts/package_post.py <项目> --dry-run
<python> <skill>/scripts/package_post.py <项目> --prepare-review
<python> <skill>/scripts/package_post.py <项目>
```

`present_round` 是每个用户可见轮次的统一入口，返回 gallery、manifest、打开状态和下一步。`--no-open` 只生成页面与路径，不宣称看过。最终页必须区分当前、上次交付、比较基线、已确认、待审、缺图和失效。

默认 `copy.image_text=none`、overlays 为空；只有用户明确要求图字时才允许 `user-approved`。`--prepare-review` 只输出实际排版待审图，不提交 post；这组新像素通过独立物理审查后才能正式打包。九宫格预览在 `post/previews`，正式图仍在 `post/` 根。旧版保留为 `post-previous-*`。

## 恢复与失败

先运行 `show`，再查看当前轮缺项、decision receipt、pending_intents 和交付 manifest。生图失败不重新 init、不删除已通过方向；成功响应后的下载中断先用 `image_api.py resume`，没有成功响应不要自动重发可能收费请求。事务日志存在时，下一次写入或读取恢复完整旧版或完整新版；不要手工删 `.state-transaction.json`。

`show.next` 只给当前动作：ask、backend、round、generate、record-round、review、await-choice、package 或 done。planned 轮已有图片时先登记或恢复待审，不重复生成；历史 stage=done 但当前源图或发布输出缺审时返回 review，保留历史状态和文件，不误称当前已完成。不要用新建项目、继续生成或删除旧文件来掩盖未解决的阻断。
