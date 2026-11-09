# 产品、镜头与实际图验收

本文件负责商品用途、设计尺度、视图资料、逐图镜头、生成任务和 G1。项目事务见 [项目状态](project-state.md)，匿名物理 G2 见 [物理审查](physics-review.md)。这些字段由 Agent 从自然语言整理，不要求用户填写工程表单。

## 先保存已经知道的事实

一段话可以同时提供品类、主体、用途、尺度和保持项。用 `set` 保存普通答案，用 `product` 保存主体的产品约定；只有当前缺失或冲突的内容才问。用途保留自由文本，桌摆兼可拆包挂无需勉强归入单选。新增扣件、挂点或其他结构先提案，不能把推断写成原稿事实。

```text
<python> <skill>/scripts/project.py product <项目> --subject <主体名> --file <规格.json>
```

规格文件是增量对象，可只提供其中一个字段。示意：

```json
{
  "usage": {
    "text": "桌面摆放，也能拆装挂绳用作包挂",
    "status": "confirmed",
    "origin": "agent-inferred"
  },
  "size": {
    "mode": "numeric",
    "height": 12,
    "unit": "cm",
    "measurement_basis": "本体站高，不含挂绳与扣件",
    "status": "confirmed",
    "origin": "agent-inferred",
    "evidence_kind": "design_target"
  }
}
```

本例是模拟选择，故为 `agent-inferred`；用户直接提供的规格或设计才记 `user-confirmed`。用户认可 Agent 新提案时，保留提案来源 `agent-inferred`，另记确认状态、认可者和依据；不要把认可写成原稿事实。`status` 与 `origin` 是两件事，确认不会改变事实来源。

`confirmed` 表示本流程已明确采用的方案，不自动表示真人亲自批准；页面同时展示来源。被明确授权的设计决定或模拟试用可用 `agent-inferred`，证据记录授权范围；用户尺寸或结构被改变、未授权的推断须先提案确认。禁止为过门禁把推断改写为用户原话。

`size.mode` 支持 `unknown`、`numeric`、`range`、`qualitative`。数值和范围支持 `mm/cm/m`，范围用两个正数的数组；程序归一到毫米但保留原值。定性模式用 `description` 写可观察目标，并写 `measurement_basis`。未知尺寸可以继续概念与材质；相关场景前须有认可目标。

`evidence_kind` 为 `design_target`、`user_specification` 或有明确依据的 `measured_sample`。效果图不能证明厘米，手掌大小也不是尺。图片像素、商品尺寸、画面占比各自管理；给不出可靠参照时，G1 应明确判断证据是否足够，不能拿提示词或元数据代替实际观察。

`product_contract.subjects/specs/view_assets` 使用稳定 ID。`show` 返回这些映射；镜头的 `spec_ref` 用项目 JSON pointer 指向对应规格。多款式显式传 `--spec`，不能把不同尺度自动合并。

## 按需补视图

保留正面主资产。只为已要求的镜头补必要侧背资料；不会看见的隐藏面无需提前生成全套。未知背部先给设计提案，确认后再作为设计目标引用，不宣称还原了原稿未提供的事实。

```text
<python> <skill>/scripts/project.py view <项目> record --subject <主体> --view back --path <项目内图片> --origin agent-inferred
<python> <skill>/scripts/project.py view <项目> confirm --asset <返回的资产ID> --origin agent-inferred --evidence "模拟试用选择：认可此背部设计方案"
```

登记时验证真实图片与 SHA-256，状态仍为 proposed；确认是独立操作，重新验证图片与产品约定。若产品规格或图片改变，旧确认不能直接复用。可用视面为 `front/three-quarter/left/right/back/detail`。离散二维预览不能称为可旋转的三维模型、工程图或打样文件。

## 每张图有自己的镜头

在笔记类型、交付形式已知后建立计划：

```text
<python> <skill>/scripts/project.py scene-plan <项目>
<python> <skill>/scripts/project.py scene-plan <项目> --file <自定义计划.json>
<python> <skill>/scripts/project.py compile <项目> --slot cover
```

首条从笔记图位生成默认计划；已有计划时只读返回。自定义计划必须在首次建立时提供；之后只能增量修改。计划包含 `schema_version:1`、`revision:0`、`slots` 和 `coverage`。每个 slot 有稳定 `id`、`role`、主体/规格引用、`view`、`yaw`、`elevation`、`pose`、`framing`、`reference_bindings`、`scale_anchor`、`keep`。方位、相机高低、产品姿态、取景独立；不因用户说“比如45°”就替换全部正面。

`coverage.required_views` 是本套需要的视面，`coverage.preserve_slots` 是保持图位。G0 只能证明计划格式与计划覆盖，不能证明图片真的有该角度。`compile` 按图位生成提示词和分工参考：身份、产品、视图、环境、风格、基线。环境或风格参考不能携带机位与姿态指令。生产编译会验证真实参考文件和摘要；明确侧背镜头缺少当前已确认资料时停止。

slot 可选 `constraint_policy`，映射仅限 `framing/yaw/elevation/pose/scale_anchor/description`，每项必须包含 `level:required|advisory`、`origin:user-confirmed|agent-inferred|reference-derived` 和非空文本 `evidence`。只有 `agent-inferred` 可标 `advisory`；用户要求、参考来源、身份、产品规格与尺寸、role、view、keep、coverage 不可降为建议。Agent 自设的占比、留白、道具位置须先核对明确出处，再通过显式 `scope` 补丁附证据校准；不得改写用户要求。旧计划不补造 policy，未标字段保持旧行为，不自动降级。

`compile_slot` 仅在 slot 显式带 policy 时返回其副本，提示词逐字段区分硬合同和非硬合同建议。建议值可调整，但实际认脸完整性、目标视角、职责所需信息量和尺度相容性仍须满足；尺度锚点建议不能改变产品尺寸，也不能替代实际尺度证据。

## 追加和局部回改

```text
<python> <skill>/scripts/project.py scene-patch <项目> --file <补丁.json>
```

补丁必须写 `schema_version:1`、`op`、`scope.slots` 和 `expected_revision` 或 `expected_hash`。为外部重试保存稳定 `request_id`：同请求同内容读回 committed 回执，改内容拒绝；不带 ID 的原样重试也按完整指纹判重。已有后续修改时保留历史回执并重新规划，不调整旧请求 revision 来伪装新操作。操作与内容：

| op | 内容 | 影响 |
| --- | --- | --- |
| append | `slots:[新图位...]` | 原图位与字节保留，只补新图 |
| edit | `slot_id`、`changes` | 只失效指定图位 |
| replace | `slot_id`、`slot` | 原稳定 ID 不变，明确替换范围 |
| reorder | `order:[全部现有ID]` | 不重新生图，完整排列须重新验收 |

例：在原图组之后增加背面；真实 `spec_ref` 从 `show` 复制：

```json
{
  "schema_version": 1,
  "op": "append",
  "expected_revision": 0,
  "scope": {"slots": ["back-new"]},
  "slots": [{
    "id": "back-new",
    "role": "angle",
    "subject": "主体名",
    "spec_ref": "/product_contract/specs/从show取得的实际款式ID",
    "view": "back",
    "pose": "自然站立",
    "framing": "完整全身",
    "keep": ["已确认的身份、配色与结构"]
  }]
}
```

需要保护已定稿图时用 `preserve_slots`，并在 `baseline_hashes` 声明相应实际摘要；编辑/替换已定稿目标也必须声明当前摘要。程序核对最后时刻文件。默认追加不会删正面；满九宫格拒绝新增，先解决改整套、明确替换或拆组的冲突。不能静默覆盖或重生成整套。

补丁返回 `impact`，包括新增、失效、保留及字节摘要。已有定稿保留历史，受影响项标待改；重新生成只指定缺项/待改项。追加或重排后，旧单图适用审查可复用，但完整集合的覆盖与顺序必须重新审。

保护图位允许 `edit` 的 `changes` **仅含** `constraint_policy`；不能夹带其他属性或用 `replace` 绕过保护。有 final 时仍须核对并声明当前 baseline hash。此时 `changed_slots` 包含目标，`invalidated_slots:[]`，目标仍计入 `preserved_slots/preserved_hashes`，`sequence_changed:true`、`requires_visual_review:true`；只更新审查约定，保留图片。已有 `required` 的用户或参考来源 policy 不能删除、降级或改标来源。

旧约定先明确审查出处并保留证据，再以新 `request_id` 提交局部校准补丁（`changes.constraint_policy` 是完整映射，保留其中已有的受保护项）。修改后的图位与编译签名会变化，须创建新的独立 G1 request 按当前约定看实际图片；不得重放旧请求、删旧负评或把旧失败改为 pass。本规则适用于所有满足这些条件的图位，不为特定图片豁免。

## 原生生图前登记任务

默认宿主原生/系统生图，不需要 Key。生成任务防止本地恢复时重复发起；不承诺外部服务 exactly-once。非场景阶段用 `--file` 提供真实的编译对象，内含本次实际提示词及参考；场景可不传文件，按 `--slot` 自动编译。

```text
<python> <skill>/scripts/project.py task <项目> prepare --slot <稳定图位或候选ID> --intent <本次明确意图ID> --file <编译.json> --authorized
<python> <skill>/scripts/project.py task <项目> prepare --slot cover --intent <本次明确场景意图ID> --authorized
<python> <skill>/scripts/project.py task <项目> begin --plan <返回的项目内计划路径> --authorized
```

只有 begin 返回 `should_call:true` 才调用原生工具。先保留 `task_id/claim_id`，调用后将真实输出保存项目内：

```text
<python> <skill>/scripts/project.py task <项目> complete --task-id <ID> --claim <claim_id> --tool-request <真实工具回执ID> --output <项目内图片>
<python> <skill>/scripts/project.py task <项目> status --task-id <ID>
<python> <skill>/scripts/project.py task <项目> unknown --task-id <ID> --claim <claim_id> --reason "工具结果尚未确定"
```

`--tool-request` 优先使用工具真实返回的请求 ID 或产物标识，不虚构服务端编号。工具只有输出字节且无任何标识时，可省略该参数；CLI 用 `local-artifact:<sha256>` 并记录 `receipt_kind=local-artifact`、`server_request_id=null`，这仅是本地追溯，不证明服务端请求身份。不得因此改用 API。

任务身份绑定项目、图位、意图、实际编译内容、参考字节与后端。恢复已完成结果先查摘要；requesting/unknown 不自动重发，先核对工具回执与已有文件。明确重新生成使用新的意图，保留旧任务。不能用补写 receipt 冒充调用前登记，也不能用本地哈希证明服务端真实生成或图片质量。

新轮的生成项在 `record/select/final` 及发帖前重新核对本地完成账本、实际输出、提示词和参考。将本次实际调用对象写入 `item.compiled`，最终提示词须与 `item.prompt` 及任务一致；用 `item.generation_task` 明确任务 ID，省略时仅按实际路径和摘要寻找唯一匹配。不能增强提示词后仍保留旧编译对象。历史轮和已认可输入不补造生成凭据；`derive/compose` 保留真实 sources，不伪称新生图。

只有确认外部服务未执行生成的明确失败，才可 `task <项目> failed --task-id <ID> --claim <claim_id> --reason <依据> --retryable`，再用 `begin --retry --authorized` 显式重试。超时、进程退出或丢响应一律先 unknown，不得标成安全重试。

## G1 与 G2 分开

场景进入 record/select/final 之前以及实际发布集合均需 G1。先准备中性合同包：

```text
<python> <skill>/scripts/contract_review.py prepare <项目> --images <项目内图片...> --producer <实际创作者ID> --stage scene --ordered-slots <映射.json>
<python> <skill>/scripts/contract_review.py record <项目> --request <请求ID> --report <独立报告.json>
<python> <skill>/scripts/contract_review.py status <项目> --images <项目内图片...> --stage scene --ordered-slots <映射.json>
```

映射按实际顺序列 `{"slot_id":"cover","path":"rounds/.../cover.png","source":"rounds/.../cover.png"}`。场景候选使用本轮 `review_paths` 的虚拟 ID；正式发布用 `package_post --prepare-review` 返回的实际输出映射文件，不能猜编号对应关系。

G1 独立执行者只读生成的请求、任务说明、清理后的真实图片及必要合同参考。逐图看 identity/scale/view，逐集合看 coverage/preservation/order，记录真实 observed_views 和可见证据。结果格式由包内 task 定义，禁止仅返回总 pass。未知、缺审、不确定、篡改或过期均阻断。

候选或增量批次只验本批图位，不误要求尚未生成的全套；最终 publication 必须验完整图序和约定覆盖。认脸封面、微距没声称厘米时不强制每张放尺具；按本图职责检查可见比例与合同未冲突，说明不验证绝对厘米。实际尺度图、大小款比较及最终集合仍须约定的尺度证据，证据不足判 uncertain。

G2 必须由另一个新上下文执行者完成，只读匿名物理包；不带产品合同、Bible、创作提示词或 G1 结论。程序验证声明、摘要和适用性，无法证明审查者真看过图；因此实际独立看图的执行记录也要保留。工程测试报告和真实视觉验收分开。

发帖先准备实际输出，G1 核对完整集合和图序，G2 核对适用的新像素，再正式打包。文案改动不重编码同一图片；更换源图、裁切、字体或其他渲染输入后必须重新验证。门禁只放行适用的 pass，不能靠删需求、改名称、手动 final 或沿用旧 post 绕过。
