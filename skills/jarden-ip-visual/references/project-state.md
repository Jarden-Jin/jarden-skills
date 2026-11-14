# 项目状态、产品约定与恢复

项目状态以 `project.json` 和已登记的审查、选择回执为准；HTML、浏览器草稿与生成任务回执不等于批准。JSON 使用 UTF-8，命令返回 JSON。`<项目>` 是目录参数，含空格时作为单个参数传入；图片路径通常相对项目根。从 [主流程](../SKILL.md) 进入本文件；默认资产范围与停止条件见 [按需资产工作流](asset-workflow.md)，只有 legacy 或确实涉及商品的分支才读 [产品与镜头](product-and-shots.md)。

## 目录

- [schema、版本与事务](#schema版本与事务)
- [工作流路由](#工作流路由)
- [项目结构](#项目结构)
- [建立、查看和保存已知答案](#建立查看和保存已知答案)
- [商品用途、设计尺度与视图来源](#商品用途设计尺度与视图来源)
- [legacy 镜头计划、增量修改与 compile](#legacy-镜头计划增量修改与-compile)
- [原生生成与 ledger 恢复](#原生生成与-ledger-恢复)
- [legacy 开轮、G1 实际图与 G2 物理审查](#legacy-开轮g1-实际图与-g2-物理审查)
- [legacy 选择与通用反馈](#legacy-选择与通用反馈)
- [legacy prepared 实际图与正式打包](#legacy-prepared-实际图与正式打包)
- [恢复与停止](#恢复与停止)

## schema、版本与事务

- `SCHEMA_VERSION=4`，项目代码读 schema `[1, 2, 3, 4]`、写 `[3, 4]`。schema 1/2 只读，`next.action=migration`；schema 3 的 legacy 项目可以继续写入，不自动改为资产模式。带资产工作流数据的项目须使用 schema 4，未知 schema 拒绝读取和降级覆盖。
- Skill 版本只从 `SKILL.md` 的 `metadata.version` 读取，不从项目 schema、目录名或 README 推断。schema 4 同时容纳资产和 legacy 模式，不能仅凭数字判断当前工作流。
- `revision` 是业务版本，答案、选择、产品约定和审查登记等成功保存后递增。`requirements_revision` 配合 legacy 阶段需求签名使旧轮次失效；schema 3/4 的商品签名还绑定产品约定与适用镜头计划，不能只比较 revision。
- `scene_plan.revision` 是独立的计划版本；产品约定、镜头计划、生成 ledger 和交付 manifest 各自的内部 `schema_version: 1` 不代表项目退回 schema 1。
- 项目核心写入使用锁、临时文件与 `.state-transaction.json` 日志；`show` 发现未完成状态事务时会恢复。生成 ledger 另有任务锁，调用外部工具前独立落盘。不要手工删除日志或承诺整个图片生产过程都是原子事务。
- `present_round.py` 更新交付索引和打开回执，不增加业务 revision。`brief.decisions` 中的 `origin` 区分 `user-confirmed`、`agent-inferred`、`reference-derived`；确认推断方案不改写其原始来源。

## 工作流路由

| 入口或状态 | 如何继续 |
| --- | --- |
| 新 `init`，省略 `--workflow` 或指定 `assets` | 创建 schema 4 资产项目，先确认本轮资产；以真实 `show.next` 继续 |
| 新 `init --workflow legacy` | 创建 schema 4 legacy 项目，沿用概念→材质→透明商品图→场景→发帖的固定流程 |
| 现有 schema 3 legacy | 原路径继续，不强制迁移或附加资产语义 |
| schema 1/2 | 只读展示；写入前对单个项目预览并显式迁移 |
| schema 3 要转资产模式 | 先显式预览迁移到 4，再确认资产范围；结构迁移本身不等于启用资产模式 |

`next_action` 在可写检查后优先路由已启用的 `asset_workflow`，不先套用 legacy 的品类、产品用途、场景或发帖问卷。`show` 在资产模式另返回 `asset_workflow` 视图；目录、计划、真实文件来源和单项审查的命令见 [资产操作示例](asset-workflow.md#操作示例)，不手写状态或猜测动作参数。程序调用 `new_project(..., workflow="legacy")` 可显式建立旧流程数据；命令行 `init` 的默认值仍是 assets。

## 项目结构

```text
<项目>/
  project.json
  inputs/
  rounds/<轮次>/round.json
  rounds/<轮次>/A.png
  generation-plans/gen-<摘要>.json
  tasks/gen-<摘要>.json
  .workbench/presentation.json
  .workbench/server.json
  artifacts/sha256/<摘要>.<扩展名>
  deliveries/D0001/{manifest.json,index.html,receipts.json}
  decisions/<request_id>.json
  reviews/contract/<request_id>/
  reviews/physics/
  reviews/post/<批次>/{manifest.json,output-slots.json,编号图片}
  reviews/creative/ 等分类报告
  copy.json
  gallery.html
  post/
```

以上为共享记录及 legacy 商品轮次的布局，不要求每个资产项目创建全部目录。资产工作流数据保存在 `project.json` 的 `asset_workflow`；产品、视图和镜头约定在 `product_contract`、`scene_plan`，其存在不代表纯平面资产也须补齐商品规格。交付 manifest 冻结项目版本、真实上次交付、比较基线和内容寻址快照；替换源图不会替换旧交付。`receipts.json` 保存打开事件，选择提交回执另在 `decisions/` 和 `project.decision_receipts`；页面已派发打开不等于用户看过。

## 建立、查看和保存已知答案

```text
<python> <skill>/scripts/project.py init <项目> --input <图> --kind wireframe
<python> <skill>/scripts/project.py show <项目>
```

以上默认进入资产模式。需要固定商品链时，只对另一个新项目显式指定：

```text
<python> <skill>/scripts/project.py init <旧流程新项目> --kind text --workflow legacy
```

以下是保存已经知道的信息的例子，不是所有项目必填的初始化问卷；产品、笔记及交付字段仅在所选范围需要时保存：

```text
<python> <skill>/scripts/project.py set <项目> answers.product_form=plush answers.subject_scope=single
<python> <skill>/scripts/project.py set <项目> 'answers.subjects=["角色名"]' answers.concept_count=1
<python> <skill>/scripts/project.py set <项目> answers.note_type=single-multiview answers.delivery=full-post image_backend.kind=builtin
```

输入类型为 wireframe、sketch、flat-design、ai-image、photo、text、reference；纯文字用 `--kind text`，可不传 `--input`。重复 `init` 只读已有项目，传入不同 `--workflow` 也不会切换现有工作流。`--reuse-from <旧项目>` 仅沿用通用品类、笔记类型、交付方式、方向数和后端设置，不替代本项目的资产范围、用途、尺寸或身份确认。

`answers` 存已知答案；`brief` 存角色、卖点、bible、补充和保持项。新资产项目的 `preferences.directions_per_round` 默认 2，新 legacy 项目默认 3；旧项目和 `--reuse-from` 的已存设置保留，方向数不是角色数或验收门槛。legacy 品类、角色、数量变化使相应后续定稿失效，笔记和交付变化使场景与发帖失效，旧图片保留。资产模式只按实际依赖核对影响范围，不用未选笔记驱动额外生成。角色名唯一，缩减名单时同时明确数量和保留对象。

## 商品用途、设计尺度与视图来源

本节用于 legacy 商品流程，或所选资产确实包含产品尺度、视图等约定的部分；不是线稿、平涂、设定文档的通用前置。

用户已说明用途、尺度和保持项时一次保存，不重新拆问。`product_contract.subjects/specs/view_assets` 使用稳定 ID；从 `show.product_contract` 读取 ID，不自行猜测。用途保留原文，例如「桌面摆放，也能拆下挂在包上」，不能由这句话自动添挂扣结构。

```text
<python> <skill>/scripts/project.py product <项目> --subject <角色名或主体ID> --file <产品更新.json>
<python> <skill>/scripts/project.py view <项目> record --subject <角色> --view back --path inputs/back.png --origin agent-inferred
<python> <skill>/scripts/project.py view <项目> confirm --asset <返回的视图ID> --origin user-confirmed --evidence <用户确认依据>
```

`product --file` 是包含 `usage`、`size`、`structure`、`name` 中所需字段的更新对象，可用 `--spec <已有款式ID>` 指定款式。顶层字段合并，但传入的 `usage`、`size` 等对象整体替换，须带齐该对象信息。例如用户已明确同意下列设计目标后：

```json
{
  "usage": {
    "text": "桌面摆放，也能拆下挂在包上",
    "status": "confirmed",
    "origin": "user-confirmed"
  },
  "size": {
    "mode": "range",
    "height": [10, 12],
    "unit": "cm",
    "measurement_basis": "本体站高，不含挂扣",
    "status": "confirmed",
    "origin": "user-confirmed",
    "evidence_kind": "design_target"
  }
}
```

尺寸支持 `unknown/numeric/range/qualitative`；数值可填 height、width、depth、total_length，单位 mm/cm/m，范围用两元素数组；程序派生 `normalized_mm`。定性用 `description`，已知尺寸均需 `measurement_basis`。`evidence_kind` 区分设计目标、用户提供规格与样品测量；`measured_sample` 必须有 evidence。确认设计目标不能写成实测。

legacy 用途须在开轮前确认，尺寸可在概念阶段未知，相关 scene/package/done 前须确认数值、范围或可观察的定性尺度。页面自由输入支持带量取口径的单维 mm/cm 数值和同单位范围，不把范围上界误写为站高。多维文字完整保留为定性描述；Agent 需要精确多维、m 单位或实测依据时用结构化 `product`，不能声称页面能解析任意尺寸句子。

`view record` 校验真实图片并绑定摘要；`confirm` 另记确认人和依据，原 `origin` 保留。正面透明主资产继续使用，只补镜头需要的侧背资料。`compile` 对 left/right/back 检查当前款式下已确认的对应视图；未知隐藏面先提案确认。多视图是离散二维效果图。

## legacy 镜头计划、增量修改与 compile

本节是旧四类发布组合的商品镜头接口，不是资产目录的计划入口。先确认笔记类型和交付方式，再建立计划；已有计划不能用 `--file` 整组覆盖。资产模式仅在明确进入对应商品场景范围时使用，不为非发布资产强制建立整套镜头。

```text
<python> <skill>/scripts/project.py scene-plan <项目>
<python> <skill>/scripts/project.py scene-plan <项目> --file <首次镜头计划.json>
<python> <skill>/scripts/project.py scene-plan <项目> --reconcile --expected-revision <当前scene_plan.revision>
<python> <skill>/scripts/project.py scene-plan <项目> --reconcile --expected-hash <scene-plan返回的signature>
<python> <skill>/scripts/project.py scene-patch <项目> --file <增量修改.json>
<python> <skill>/scripts/project.py compile <项目> --slot <真实图位ID>
```

不传 `--file` 时，`scene-plan` 首次从笔记模板建立计划，之后只读返回当前计划、signature 和 reconciliation。计划结构是 `schema_version: 1`、`revision`、`slots`、`coverage`。每个 slot 必有稳定 `id`、`role`、`view`；可填 subject/subjects、指向具体款式的 spec_ref/spec_refs、yaw、elevation、pose、framing、scale_anchor、keep、description 和 reference_bindings。`coverage.required_views`、`coverage.preserve_slots` 明确覆盖与保持目标；默认计划不自动要求正侧背三视图。

笔记类型、交付方式、主体数量或九宫格设置变化后，若 `next.action=scene-plan` 且 `issue=profile-change`，先读差异，再在用户确认协调后执行 `--reconcile`。`--expected-revision` 必须匹配当前 `scene_plan.revision`，或 `--expected-hash` 必须匹配上次 `scene-plan` 输出的 `signature`（计划 hash）；两者至少提供一个，过期或冲突时拒绝写入。协调只应用明确的新默认 profile：保留自定义、已有图、历史轮次、保持项、引用和未知字段，清洁移除的默认图位归档到 `scene_profile_history`；不会自动生图，也不把可复用图位标成待重做。输出的 `impact`、新 revision 和 `next` 必须读回；重复同一协调是 unchanged，不创建任务。容量冲突或覆盖退化时停止，改用 `scene-patch` 或重新确认范围。

追加是 JSON 中的 `op: append`，没有 `scene-patch append` 子命令。以下示例须替换为实际图位、款式 pointer、计划 revision 与已定稿图片摘要：

```json
{
  "schema_version": 1,
  "op": "append",
  "expected_revision": 0,
  "scope": {"slots": ["back-extra"]},
  "preserve_slots": ["cover"],
  "baseline_hashes": {"cover": "<当前封面SHA-256>"},
  "slots": [{
    "id": "back-extra",
    "role": "angle",
    "subject": "角色名",
    "spec_ref": "/product_contract/specs/<实际款式ID>",
    "view": "back",
    "yaw": 180,
    "elevation": 0,
    "pose": "自然站立",
    "framing": "完整主体",
    "keep": ["保留已确认的色区与结构"]
  }]
}
```

其他 `op` 为 edit（slot_id/changes）、replace（slot_id/slot）、reorder（包含所有现有 ID 的 order）。都要求 `scope.slots` 精确覆盖目标，以及 `expected_revision` 或 `expected_hash`；它们校验计划版本，不是业务 revision。修改已定稿图位或保护已定稿图时须声明 baseline_hashes，CLI 从实际文件计算当前值核对。append 保留原图，edit/replace 只标记目标待改；reorder 保留字节但使整套顺序待验。九宫格满额拒绝追加，不自动删图腾位置。

G0 是生成前的计划检查：确认所需视面、尺度锚点、保持项、参考范围与缺项。`coverage_status` 和 `compile` 只证明计划及绑定有效，不证明图片达标。`compile` 输出 `compiled.prompt/bindings/signature`，分别约束方位、俯仰、产品姿态与取景，校验实际参考字节。环境参考只管场域和光线，风格参考只管视觉语言，不复制其机位或姿态。JSON pointer 用真实路径，不绑定整个合同替代具体款式。

## 原生生成与 ledger 恢复

默认 `image_backend.kind=builtin`，由宿主原生/系统 `imagegen` 调用，不索取 Key；不可用则保存待生成状态。路径、失败或质量问题不能触发自动换供应商。外部服务须由用户明确选择，另读 [图片后端](image-backends.md)。

生成授权、防重和未决对账是共享保护；具体提示词、参考和生成范围来自当前工作流的计划，不因复用 ledger 就转入 legacy 商品链。

生成 ledger 在 `tasks/gen-*.json`，计划在 `generation-plans/`。以下 CLI 自身不调用图片工具；`--authorized` 只记录已经获得的授权：

```text
<python> <skill>/scripts/project.py task <项目> prepare --slot <稳定图位或主体标识> --intent <本次生成意图ID> --file <compiled对象.json> --authorized
<python> <skill>/scripts/project.py task <项目> begin --plan generation-plans/<任务ID>.json --authorized
<python> <skill>/scripts/project.py task <项目> complete --task-id <任务ID> --claim <claim_id> --output rounds/<轮次>/A.png --tool-request <真实工具请求ID>
<python> <skill>/scripts/project.py task <项目> unknown --task-id <任务ID> --claim <claim_id> --reason <结果未决原因>
<python> <skill>/scripts/project.py task <项目> status --task-id <任务ID>
```

`--file` 接受原始 compiled 对象，不能把 `compile` 的整个 `{ok, compiled}` 响应直接当请求；省略时按 `--slot` 编译当前场景。概念、材质与候选变化须提供本次实际 prompt 和 references/refs 或含 path 的 bindings，不能直接拿相同模板充当不同请求。`prepare` 模块函数只读，但 `project.py task … prepare` 会保存计划文件。

`task begin` 按计划内容分流：资产计划含 `compiled.asset_instance` 时由资产工作流校验实例、资产范围、需求签名和已批准上游；普通 scene 计划由 `workflow_safety.validate_current_plan` 重新编译当前场景合同；非 scene 的 `task prepare --file` 计划带 `router_context`，按当前 stage、subject、round 和需求签名校验，不把旧 concept/material CLI 改成 scene 语义。所有新 claim 前都必须先过当前依赖校验；规格、提示词、参考字节或相关需求已过期时在 claim 前拒绝，不获得 `should_call`。已有 completed/requesting/unknown 先按 ledger 恢复和对账，不能用新 intent 绕过过期计划。

只有 `begin` 当次返回 `should_call=true` 才调用宿主工具；调用前 requesting 已落盘，保存返回的 task_id、claim_id。键绑定 project_id、slot_id、intent_id、编译语义与真实参考字节、后端描述；改输出路径、移动项目或重开页面不产生新的调用资格。后端描述禁止凭据。

| 恢复结果 | 必须做什么 |
| --- | --- |
| completed 且 outputs_valid | 复用原字节，继续验收和登记 |
| requesting / unknown | `resume_status=unknown`；核对工具回执及已有文件，不重发；找回真实输出后可 complete |
| output_invalid | 找回或核实已完成输出，不以缺文件为由重发 |
| failed | 只有确定失败且 retryable，并显式 retry，模块才允许重试 |
| 明确重新生成 | 使用新的 intent_id，并保存该次实际授权与请求 |

CLI 的 `task failed --task-id <ID> --claim <claim> --reason <依据> --retryable` 只用于明确外部未执行的安全失败，再 `task begin --plan <计划> --retry --authorized` 才能重试。超时或丢响应必须 unknown，不能用 failed 洗掉未决调用。ledger 校验图片结构与摘要，不证明视觉质量或服务端来源，也不替代 G1/G2。可选 API 的成功响应后下载中断另走 `image_api.py resume`；它不是原生工具 ledger 的恢复入口。

## legacy 开轮、G1 实际图与 G2 物理审查

以下 `new-round`、`accept-input`、`record-round` 属于 legacy 固定阶段接口；资产模式按其真实 `next` 与单项合同推进，不用旧开轮命令替代资产实例登记。

```text
<python> <skill>/scripts/project.py new-round <项目> --stage concept --subject <角色> --count 3
<python> <skill>/scripts/project.py accept-input <项目> --stage concept --subject <角色> --path inputs/design.png
<python> <skill>/scripts/project.py record-round <项目> <轮次>
```

`round.json` 每项填 title、idea 和完整 prompt，真实结果落在 image 指定位置。generate 的 prompt 至少 40 字符，compose/derive 须有 sources。concept/material 用 alternatives，cutout 用 set；scene 先确认场景方向，再按 scene_plan 的图位做 set。已有合格资产可 accept-input，仍检查前置阶段、透明 alpha 和适用场景审查。

直接采用已有透明主资产还需有效独立 `identity` 报告，核对完整正面全身、身份、材质和边缘；用 `--evidence` 保存真实认可或用户授权模拟决定依据，`--origin` 如实记录。没有报告、仅真 alpha 或口头写已确认不能旁路；普通新做透明资产仍走原轮次检查和选择，不自动增加全部类别审查。

```text
<python> <skill>/scripts/review_checks.py prepare <项目> --category identity --producer <制图执行者> --stage cutout --images <项目内透明图>
<python> <skill>/scripts/review_checks.py record <项目> --category identity --request <review_id> --report <独立看图报告.json>
<python> <skill>/scripts/project.py accept-input <项目> --stage cutout --subject <角色> --path <项目内透明图> --origin agent-inferred --evidence "已授权模拟决定的实际依据"
```

G0 后生成真实图，再独立 G1，随后由另一位隔离审查者做 G2。所有场景备选、编辑、合成与最终发布图都适用；缺审、uncertain、失败或摘要失效均不能登记通过、选择或手动 final。

```text
<python> <skill>/scripts/contract_review.py prepare <项目> --stage scene --images <实际图1相对路径> <实际图2相对路径> --producer <制图者ID>
<python> <skill>/scripts/contract_review.py record <项目> --request <G1请求ID> --report <G1报告.json>
<python> <skill>/scripts/contract_review.py status <项目> --stage scene --images <实际图1相对路径> <实际图2相对路径>
<python> <skill>/scripts/physics_review.py prepare <项目> --round <轮次> --producer <制图者ID>
<python> <skill>/scripts/physics_review.py record <项目> --request <G2请求ID> --report <G2报告.json>
```

G1 只送 `packet/`：中性产品合同、逐图计划、真实输出和必要参考；不送 binding、作者提示词、偏好或预设通过结论。实际查看身份、结构、尺度相容性和镜头，逐图报告 identity/scale/view、observed_views 与可见证据；整批报告覆盖、保持图和实际顺序。字段 yaw、文件名、不同哈希或手掌不能证明实际视角与厘米尺寸。准备器当前只接受合法非交错单帧 PNG，参考图也一样；其他格式须真实转码并绑定，不能只改后缀。

scene alternatives 的 `round.review_paths/review_slots` 给每个候选独立审查 ID，不改原场景计划。G1 可自动匹配唯一图位；排版输出必须用 `--ordered-slots <JSON映射列表文件>` 显式逐图同序绑定 slot_id/path/source 和摘要。完整发布集合另用 `--stage package`，请求内部 stage 是 publication，不能拿场景报告代替。

G2 只送另一份匿名 `packet/`，含完整图片、已确认品类与通用物理任务；不送 mapping、Bible、尺寸合同、G1 判词或创作上下文。检查支撑、连接件、人体接触、光影和整体可信度；详细规则见 [物理审查](physics-review.md)。两类程序各自拒绝制图者自批；G1 status 还会核对同图 G2 的 reviewer_id，相同则阻断。实际上下文隔离仍由宿主保证，没有独立看图能力就停在待审，不能伪造报告。

G1 逐图证据绑定实际字节与适用合同/参考/图位，整套证据另绑定集合与顺序；局部改动可保留无关图证据，但须重验整套。G2 按品类上下文和图片哈希复用。程序只能检查摘要、范围、报告与看图声明，不能证明审查实际独立或视觉判断正确。

## legacy 选择与通用反馈

以下 `select` 和基于旧轮次的 `new-round` 用于 legacy。`feedback` 保存原话和影响范围，`submit` 仍须通过服务端支持的协议；不能将资产目录编号直接当成 legacy 候选 ID，或以方向意向冒充正式批准。

```text
<python> <skill>/scripts/project.py select <项目> <轮次> A
<python> <skill>/scripts/project.py select <项目> <轮次> --approve --skip <待重做图位ID>
<python> <skill>/scripts/project.py new-round <项目> --stage material --based-on <轮次>:B
<python> <skill>/scripts/project.py feedback <项目> --file <反馈.json>
<python> <skill>/scripts/project.py resolve-intent <项目> --request-id <ID> --status completed
<python> <skill>/scripts/project.py submit <项目> --request <请求.json>
```

真人选定才用默认 user-confirmed；Agent 模拟或推断明确 `--origin agent-inferred`，参考材料用 reference-derived。superseded 不能重新激活。局部场景修改先 scene-patch，再 `new-round --stage scene --kind set --slots <逗号分隔图位>`；未受影响图继续用有效字节。

legacy 提交协议自身 `schema_version=1`，绑定 request_id、project_id、delivery_id、expected_revision 和当前 round_id。图像动作还带 items，保持项与备注用 keep/note；answer-field 带当前 field/value，round_id 为空。legacy action 为 answer-field、select、approve-set、request-variation、request-slots、request-redo。资产操作以当前服务端实际支持的请求为准，不猜测动作或绕过回执。相同 ID 和内容返回同一回执，内容冲突、旧版本、旧交付、换图和伪造图位拒绝。

补图/重做只登记 pending_intents，不等于生成授权。feedback 保存原话、解释、可观察改法、保持项、scope 和比较基线；不自动换角色或撤销全部确认。真实 `committed=true` 已执行选择，不重复 select；静态草稿交回聊天后才提交一次。

## legacy prepared 实际图与正式打包

本节用于已选择的旧四类发布组合，不是资产模式的通用导出器。线稿、文档和素材库按所选类型交付，不强制转成 3:4 发布图、`copy.json` 或 `post/`。

```text
<python> <skill>/scripts/package_post.py <项目> --dry-run
<python> <skill>/scripts/package_post.py <项目> --prepare-review
<python> <skill>/scripts/contract_review.py prepare <项目> --stage package --images <返回images按原顺序展开> --ordered-slots <项目>/<返回ordered_slots_file> --producer <制图者ID>
<python> <skill>/scripts/physics_review.py prepare <项目> --images <同一批实际输出路径> --producer <制图者ID>
<python> <skill>/scripts/package_post.py <项目>
<python> <skill>/scripts/present_round.py <项目> --no-open
```

范围已授权直接 prepare-review，它已含源图、文案、G1/G2 预检；只诊断才单独 dry-run。prepared 写入 `reviews/post/` 的实际排版图、manifest 和 output-slots.json，不提交 post 或业务状态。返回 images、output_slots、ordered_slots_file、render_signature、signature 和 reused；这些是待审输出，不是通过声明。`--ordered-slots` 的文件由当前工作目录解析，返回值相对项目根，须如示例补项目路径。

对完整实际排列做 G1 publication，对尚无有效报告的实际字节做 G2，分别 record 后再正式打包。正式整理按相同 render_signature、render_inputs、源图/overlay 与 output_sha256 找到 prepared 或已有 post，复制同一 PNG 字节，不再次渲染；临时目录和改名不使合格报告失效，但仍核验实际映射、当前合同和顺序。

只改标题/正文/tag 等且像素输入不变，可复用图片；文案与 package signature 仍重新校验。有图字时实际字体摘要、裁切/尺寸、源图、overlay、Pillow 或渲染代码变化使缓存失效，新像素重新审查。无图字不解析未使用字体。默认 image_text=none、overlays 为空；用户明确要求才允许 user-approved。

已有不同或损坏 post 时核对后加 `--force`，保留 `post-previous-*`；同签名完整包也重验当前门槛，不凭缓存豁免。正式图按 scene_plan 顺序编号，首图必须 cover；九宫格正好九张，预览在 post/previews。交付 caption.md、manifest.json 与编号图后再呈现工作台。

## 恢复与停止

先 show，再检查当前工作流、pending_intents、已提交回执、ledger 与交付 manifest；不要重新 init 或删除已通过方向。资产模式以 `asset_workflow` 和真实 `next` 核对所选范围的完成与缺项，不强制到旧 `stage=done`。legacy 的 `next` 除 ask/backend/round/generate/record-round/review/await-choice/package/done，还可能为 migration、contract、scene-plan、resolve-intent。legacy planned 轮真实图片已齐时先补审/登记，缺图可 `record-round --allow-missing` 展示，但不能选缺图或绕过已存在图的审查。

```text
<python> <skill>/scripts/workbench.py <项目> --serve
<python> <skill>/scripts/workbench.py <项目> --no-open --record-ready
```

workbench 可复用有效交付、回执和本机服务，`--record-ready` 只在 next=record-round 时登记已有图，不生成、不选择、不自动审查或打包。有 requesting/unknown 或 output_invalid 且原 next=generate 时，返回 reconcile-task 与原 project_next。其 automatic_generation、should_call 始终 false；详细行为见 [HTML 交付](html-delivery.md)。

legacy 历史 stage=done 不保证当前可发布；缺图、缺审、合同或输出变化时保留旧文件，按当前 next 补审或整理。安装代码与迁移项目是两件事：schema 1/2 写前，或用户明确要求将 schema 3 升到 4 时，先在单个项目外生成计划，再明确执行：

```text
<python> <skill>/scripts/migrate_project.py <项目> --out <项目外计划.json>
<python> <skill>/scripts/migrate_project.py <项目> --apply --plan <项目外计划.json>
```

预览不改项目；当前 `migrate_project.py` 目标为 schema 4，执行核对 project_sha256 和完整计划，备份原 project.json 到 backups/migrations/，保留未知字段、原图、来源、选择、审查与回执；重复执行 unchanged。结构迁移不会自动初始化资产工作流或把旧效果图改成历史草稿；迁移到 4 后通过有真实范围依据的 `assets <项目> choose` 显式启用，不能只改 schema 数字或重新 init 代替工作流切换。新用途、尺寸、隐藏面和 G1 资格不补造，缺项继续待确认。代码更新、回滚不扫描或迁移项目，见 [更新说明](updates.md)。
