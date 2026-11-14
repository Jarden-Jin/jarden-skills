# 按需资产工作流

从 [主流程](../SKILL.md) 进入本文件。它负责资产范围、投入分级、来源和完成条件；schema 与事务见 [项目状态](project-state.md)，商品场景才读 [产品与镜头](product-and-shots.md)。

## 目录

- [先理解，再确认范围](#先理解再确认范围)
- [计划只包括必要工作](#计划只包括必要工作)
- [探索、正式采用与发布](#探索正式采用与发布)
- [操作示例](#操作示例)
- [停止、交付与恢复](#停止交付与恢复)

## 先理解，再确认范围

先区分用户输入里的身份、风格、产品和传播参考，整理「保留什么、借鉴什么、开放设计什么、本轮用于什么」。接收文字、图片和账号／笔记参考；无法读取的资料标缺项，不假装已经看过。

生成前展示四组 21 项的稳定编号、交付类型和当前支持状态。用户已明确点名的项直接登记并回显，不重复选择；没有给范围时再请用户选编号。保存一次设计请求的范围，内部轮次沿用，追加只回显差量。

唯一机器目录为 `assets/asset-catalog.json`；清单、计划和页面须使用同一份定义。下表说明交付合同，不代表 21 项已全部实现或已通过真实创作验收；逐项支持状态以目录和实际证据为准。

| 编号 | 资产 | 合同与边界 |
| --- | --- | --- |
| 01 | 概念草图／创意草稿 | 方向有实质差异，草图可以保留草图质感 |
| 02 | 定稿精细线稿 | 轮廓及身份可用；位图不冒称矢量源稿 |
| 03 | 结构定位线稿 | 五官基准、比例和部件关系的设计示意，非裁片 |
| 04 | 固有色平涂稿 | 基础色区清楚，不含光影渲染 |
| 05 | 多版配色方案 | 复用有效线稿，仅改变约定色区 |
| 06 | 光影分层源文件 | 待支持；真实工程和基础色、主光、辅助光、环境光层须可编辑与重合成 |
| 07 | 最终质感效果图 | 对应选定造型、配色和材质；合成位图不是实物 |
| 08 | IP 核心设定文档 | 已知身份、性格、来源与决定一致，未知不编造 |
| 09 | 标准三视图 | 共同比例、基准和侧背对应；离散二维设计参考 |
| 10 | 视觉规范手册 | 数字色值注明色彩空间，元素规则及禁用明确；不代替实物面料对色 |
| 11 | 可复用素材库 | 素材可独立使用，绑定角色版本与来源，适用项核对真透明 |
| 12 | 产品落地效果图 | 绑定产品设计目标；与 07 同字节满足合同可引用复用，不是大货证明 |
| 13 | 工艺材质标注图 | 有来源的设计参考，标待厂确认项，非可直接制造文件 |
| 14 | 细节放大说明图 | 保留真实来源；看不清处另作设计，不伪造原图细节 |
| 15 | 尺寸规格图 | 区分设计目标与实测，本体／含配件口径明确，未知待确认 |
| 16 | 系列风格主视觉海报 | 按所选用途适配，缩略图认脸，不自动加未经批准图字 |
| 17 | 场景化延展插画 | 身份连贯，可采用独立插画语言；不强制商品渲染 |
| 18 | 合集排版素材 | 复用有效图，名称、数量和图序对应 |
| 19 | IP 表情包／小贴纸 | 小尺寸可辨；要求透明时须真 alpha 与边缘检查 |
| 20 | 账号视觉素材 | 头像、封面、背景分别满足实际尺寸和裁切用途 |
| 21 | 配色／灵感卡 | 色值、参考与原创决定的关系可追溯 |

01—07 属设计过程，08—11 属标准规范，12—15 属产品落地，16—21 属宣发延展。分组不是制作顺序。06 不得用 PNG 合集、复制扁平图的多层文件或 Agent 自述报完成；13—15 不承诺工厂直接可用、量产或实测。

## 计划只包括必要工作

计划分列所选交付、已存在的可复用资产、必要内部依赖和预计新增生成量。内部记录不自动成为额外交付；依赖确实需要新增图片时提前说明，并核对原授权是否覆盖。纯线稿＋平涂不经过无关材质、透明商品图、场景或发帖链；只选设定文档就按文档交付。

角色母版渐进形成：先记录轮廓、身份、主色区、记忆点、保持项及来源；选择结构图才补定位基准，选择三视图才补共同基线及侧背对应，选择规范才补色值和禁用规则，选择产品资料才补尺度与连接约定。完整手册、精确坐标和额外母版图不是通用探索前置。

改一个资产只重新核对实际受影响的依赖；未受影响的文件字节、确认和适用单图审查保留。仅改正文不重新生图，改变排列须重验完整集合。已有失败记录不得因换路径、改类型或改旁图而抹掉。

## 探索、正式采用与发布

| 情境 | 必做 | 不强制 |
| --- | --- | --- |
| 探索预览 | 文件有效、真实来源、授权、防重，身份硬要求、结构可读、方向可比较 | 宣传级毛尖、复杂光影、落选项精修 |
| 正式采用 | 所选资产合同、适用身份和透明检查；商品场景通过适用 G1/G2 | 无关资产、未选规范或商品问卷 |
| 宣发交付 | 实际输出、规格、文案事实和完整图序；适用新像素审查 | 用户未选的其他发布组合 |

开放探索通常先比较两方向，明确方向或局部修改先一张；用户给定数量、预算及旧项目已存设置优先，候选数不是验收门槛。待审图可预览和留反馈，最低要求满足后可记方向意向；意向不是批准，不能满足正式下游前置。候选 A 合格、B 失败时按 A 当前资格推进，B 保持失败或待审，不强制精修。

最终草图可以保持草图质感，纯线稿、平涂、贴纸、插画按内容验收，不凭扩展名决定审查。出现产品接触、承重、悬挂或尺度声称时仍检查相应身份、尺度和可见物理；不以「探索」豁免真实风险。G1 与 G2 的独立上下文要求保持，见 [审查路由](review-routing.md)。

过程图引用真实输入、轮次、选择、修改和任务回执。后来补画的过程示意须标明本轮 AI 设计，不伪称历史手绘、原始草稿、工厂打样或实拍。未知身份资料保持未知，用户确认推断不改变原始来源。

## 操作示例

入口固定为 `project.py assets <项目> <动作>`，动作为 catalog、choose、next、new、record、approve、export、status。`<python>` 为 Python 3.10+；`--file` 路径相对当前工作目录解析，文件内的 `files`、`sources`、报告 `path` 则必须相对项目根，不能越界或经过符号链接。下列 JSON 由 Agent 写成真实文件后再传入，尖括号内容须替换为本次真实值，不是可直接提交的证据。

### 查看与选择

先用 `init` 建立资产项目。采用用户原文件时须通过 `init --input` 登记；重复 init 不追加输入。已有 schema 3 legacy 要转资产模式，先单独预览并显式迁移到 4；在 schema 4 上执行有真实范围依据的 choose 才启用资产流程，不能手改 project.json。

```text
<python> <skill>/scripts/project.py assets <项目> catalog
<python> <skill>/scripts/project.py assets <项目> choose 02 04 --evidence "<用户明确选择本轮线稿和平涂的原话>"
<python> <skill>/scripts/project.py assets <项目> status
<python> <skill>/scripts/project.py assets <项目> next
```

每一步都以 JSON 和项目文件读回为准：choose 只有返回 `ok=true` 且 status 读回相同 selected 才算范围保存；它不创建实例、不批准候选，也不代表已经生图。

需要保存主体与逐项要求时，改用 `choose --file <项目>/requests/choose-assets.json`，内容如下；这是上面 positional 选择的替代入口，不必重复运行：

```json
{
  "asset_ids": ["02", "04"],
  "subjects": ["<实际角色名>"],
  "evidence": "<用户本轮真实选择依据>",
  "requirements": {"04": {"keep": ["<用户已确认的色区要求>"]}}
}
```

默认 choose 替换所选范围，追加用 `choose 05 --append --evidence "<本轮追加依据>"`；不要用替换清单暗删未完成目标。status 返回目录、selected、plan、instances、current、next、completion。先看 plan 的内部依赖、缺项和支持边界；04 依赖当前已批准的 02，不预制无关商品阶段。

### 登记 02 用户线稿

假设真实线稿已登记为项目输入，先创建一个实例，再以 new 返回的实例 ID 登记文件：

```text
<python> <skill>/scripts/project.py assets <项目> new --asset 02 --source-kind user-input --quality final --count 1
<python> <skill>/scripts/project.py assets <项目> record --instance <02实例ID> --file <项目>/requests/record-02.json
```

`record-02.json`：

```json
{
  "files": [{"path": "inputs/<实际线稿文件名>.png"}],
  "source": {
    "kind": "user-input",
    "producer": "<真实输入提供者或制图者ID>",
    "evidence": "<文件由用户提供及其用途的真实依据>"
  }
}
```

files 不得为空；单张图片或文档实例只接收一个文件，素材库可接收多个且不能重复路径。也可在描述符中提供真实 `sha256`、`size_bytes`，程序会重新计算并核对；不能拿扩展名或占位图代替有效文件。图片须可解码、符合目录的 delivery 格式，适用透明项还须通过真 alpha；文档当前为可读 UTF-8 Markdown。user-input 必须匹配已登记输入，不把新生图冒称用户原稿。

record 返回 `ok=true`、实例文件摘要、source receipts 并由 status 读回相同实例，才算文件登记成功；record 不等于 review 通过或正式采用。文件或来源校验失败不能宣称登记成功；即使已登记，eligibility 仍可能因上游、审查或需求失效而拒绝采用，须按真实原因处理，不在聊天中写“已完成”。

### 独立审查与正式采用

record 返回真实 `review_template`；status 的 `current.items[].review_template` 也可读取。将模板保存为项目内的 `reviews/assets/<02实例ID>-design.json`，交独立执行者查看实际文件。保留 kind=asset-review、项目／实例／资产 ID、需求／上游签名和 files 的 path/sha256/size_bytes 原值，不手编或清空范围。reviewer.id 不得等于 source.producer，真实检查后才声明 independent=true、viewed_files=true。

verdict 为 pass／needs_revision／uncertain，checks 覆盖目录项全部 acceptance，每项有 verdict 和至少 8 字符可观察 evidence，全部实际通过才可总 pass。physical_applicability 含布尔 required、真实 content_kind 和至少 8 字符 reason，不照抄 CLI flat 标记豁免商品关系。

例如 02 的检查项为「轮廓可核对」「非对称特征保留」「不冒称矢量源稿」，04 则按其返回模板检查色区、无材质光影和线稿来源；不拿 02 报告套到 04。真实平面线稿可记录不适用商品物理的依据；产品、场景、实拍输入或关联上游有相应关系时仍须 G1/G2。

02 的报告正文至少应具有如下结构；其中实例 ID、项目 ID、两个签名、文件摘要和证据必须从当前实例与真实查看结果填入，不能照抄占位值：

```json
{
  "schema_version": 1,
  "kind": "asset-review",
  "project_id": "<当前project.json的project_id>",
  "instance_id": "<assets new返回的02实例ID>",
  "asset_id": "02",
  "requirements_signature": "<当前实例的64位需求摘要>",
  "upstream_signature": "<当前实例的64位上游摘要>",
  "files": [{"path": "inputs/<实际线稿文件名>.png", "sha256": "<实际文件的64位SHA-256>", "size_bytes": 12345}],
  "reviewer": {"id": "<独立审查者ID>", "independent": true, "viewed_files": true},
  "verdict": "pass",
  "checks": {
    "轮廓可核对": {"verdict": "pass", "evidence": "<实际并排查看轮廓后的可观察依据>"},
    "非对称特征保留": {"verdict": "pass", "evidence": "<实际查看左右或局部特征后的可观察依据>"},
    "不冒称矢量源稿": {"verdict": "pass", "evidence": "<依据文件格式和交付说明确认未声称矢量源稿>"}
  },
  "physical_applicability": {"required": false, "content_kind": "flat", "reason": "<实际内容为何没有产品接触、承重或悬挂关系>"},
  "claim_boundary": "程序验证本地文件、摘要、报告声明及适用范围；不证明实际独立看图、外部生图真实性、视觉质量或制造可用性。"
}
```

上例 `12345` 只表示整数类型，不是任何真实文件的大小，须从 record 返回的模板复制实际值。报告的 files 必须与当前实例一一相同，报告写完后再计算报告本身 SHA-256，供 approve 的 reviews.design 描述符引用。

独立报告实际完成后，计算其真实 SHA-256，写入 `requests/approve-02.json`。`--file` 读取的是下列包装对象，不是报告正文、裸 reviews 对象或 approved=true：

```json
{
  "reviews": {
    "design": {"path": "reviews/assets/<02实例ID>-design.json", "sha256": "<真实报告文件的64位SHA-256>"}
  }
}
```

```text
<python> <skill>/scripts/project.py assets <项目> approve --instance <02实例ID> --file <项目>/requests/approve-02.json --evidence "<用户正式采用本实例的真实原话>"
```

正式采用要求 user-confirmed 来源及真实依据，方向意向或 Agent 推断不能代替。只有 approve 返回 `ok=true`、`status=approved`，并由随后 status 读回当前实例仍具备 `eligibility.approved=true`，才可写“已采用”；页面按钮、报告存在或 `prefer-direction` 意向都不算成功。要先登记报告而暂不采用，用 `record --instance <实例ID> --file <报告包装JSON>`，其中为 `{"reviews":{"design":{"path":"项目内报告路径","sha256":"真实摘要"}}}`；失败或 uncertain 也如实保留，不改文件名、改类型或复制同字节来洗掉负评。商品图在 record 中还须带 `review_slots`，逐个将 path/source 绑定真实合同 slot_id；approve 会读取现有独立 G1/G2 的当前状态，附 g1/g2 报告描述符也不能替代其门槛，规则见 [项目状态](project-state.md)。

### 04 原生生成计划与来源

02 已实际批准后，才按下一步为 04 准备计划。保存 `requests/new-04.json`，只在用户已授权该范围时记录 approved=true，authorization.evidence 保留原话：

```json
{
  "prompt": "<本次完整平涂提示词：保留已批准线稿的轮廓与非对称特征，按真实色区要求制作，不添加材质光影；不是占位提示词>",
  "authorization": {"approved": true, "evidence": "<用户明确授权本轮平涂生图及范围的原话>"}
}
```

```text
<python> <skill>/scripts/project.py assets <项目> new --asset 04 --source-kind builtin-generation --quality final --count 1 --file <项目>/requests/new-04.json
<python> <skill>/scripts/project.py task <项目> begin --plan <04实例返回的generation_plan> --authorized
```

new 只保存实例和 `asset-plans/<任务ID>.json`，`should_call=false`，不领取、不联网、不调用 imagegen。读取返回的 generation_task、generation_plan、compiled 和上游参考，不另造任务。`cmd_task begin` 根据 `compiled.asset_instance` 分发资产计划校验，在新 claim 前核对实例、范围、需求签名及上游资格和字节；不能删除该字段让资产计划走普通场景校验。普通非 scene 的 `task --file` 计划另带 scoped `router_context`，由当前 router 校验，旧 concept/material CLI 不被改写。

只有 begin 当次返回 should_call=true，才由宿主在此 CLI 之外调用原生 imagegen；成功后保存真实输出，再登记任务完成和文件来源：

```text
<python> <skill>/scripts/project.py task <项目> complete --task-id <generation_task> --claim <begin返回的claim_id> --output assets-images/<真实平涂输出>.png --tool-request <真实工具请求ID>
<python> <skill>/scripts/project.py assets <项目> record --instance <04实例ID> --file <项目>/requests/record-04.json
```

`record-04.json`：

```json
{
  "files": [{"path": "assets-images/<真实平涂输出>.png"}],
  "source": {
    "kind": "builtin-generation",
    "producer": "<实际制图执行者ID>",
    "evidence": "<真实工具调用与输出保存依据>",
    "generation_task": "<该04实例返回的generation_task>"
  }
}
```

builtin-generation 必须绑定本实例已完成且输出匹配的 ledger；有本地图片不等于任务完成，更不证明外部服务真实执行。工具没有服务端请求 ID 时不能编造，按 [项目状态](project-state.md) 保留本地输出摘要与边界。requesting/unknown 先对账，不以新 intent 绕过；确定要新生图且没有未决请求时，new 用新的 `--intent <ID>` 和 `--evidence <真实重做依据>`，默认重复 new 会复用已有轮次。随后按 04 的实际模板独立审查并正式采用，不能直接复制 02 的报告。

### 08 文档与其他来源

只要设定文档时，在另一个新资产项目 choose 08；完成真实 Markdown 正文后按下列路径登记，不调用生图或强制商品问卷：

```text
<python> <skill>/scripts/project.py assets <文档项目> choose 08 --evidence "<用户本轮只要设定文档的真实原话>"
<python> <skill>/scripts/project.py assets <文档项目> new --asset 08 --source-kind local-authoring --quality final
<python> <skill>/scripts/project.py assets <文档项目> record --instance <08实例ID> --file <文档项目>/requests/record-08.json
```

```json
{
  "files": ["assets-documents/setting.md"],
  "source": {"kind": "local-authoring", "producer": "<实际文档作者ID>", "evidence": "<正文依据、实际编写和未知项保留说明>"}
}
```

正文须有真实身份来源和未知项边界；仍按 08 的 review_template 独立读文件并由用户正式采用。local-authoring 仅支持文档，不能代替图片生成。reference-derived、local-derive、local-compose 则须在 source 中提供非空 `sources` 数组，指向真实项目内源文件；其 kind 必须与实例 source_kind 相同且被目录允许。本地派生／合成的来源回执不证明曾调用图片接口。

### 导出与完成

```text
<python> <skill>/scripts/project.py assets <项目> export
<python> <skill>/scripts/project.py assets <项目> status
```

本轮全部所选资产有当前合格批准后才导出；02＋04 项目须两项均合格，不能只完成 02 就报整轮完成。只有 export 返回 `ok=true`、真实 `assets-output/manifest.json` 存在且随后 status 读回 `completion.complete=true`、`exported=true`，才可写“资产已导出”。导出保留已审字节到 `assets-output/files/`，写 manifest，记录来源、上游、批准、支持状态与非制造边界。失败、未支持、摘要变化或缺项不会被自动替代；导出不是发帖，也不是发行包。

## 停止、交付与恢复

按所选资产逐项判定完成，不以跑到旧 `done` 阶段或拥有某种后缀替代资格。文档交可读正文和来源，素材库交独立可复用文件与索引，图片按实际用途交规格、来源和审查证据；3:4 仅约束相应发布图，不是所有资产通用比例。

未支持或证据不足的目标保持未完成。可以请用户接受设计示意、延期或协作交接，保存处理回执；只有用户明确变更范围才按新范围结算，不把降级产物冒作原目标完成。

同系列合集、IP 设计手稿、单款多维和多角色多场景四种旧笔记保留为可选发布组合；制作依赖与图序分开，按 [阶段规则](stages.md) 和对应笔记规范处理。需要正式发帖素材时才读 [文案规范](copywriting.md) 并进入发布整理，不自动发布。

恢复先读 `show.next`、原图、适用审查、提交回执与任务账本。`requesting/unknown` 先对账，不重发；缺工具或独立看图能力时保存真实缺项，不换后端或伪造通过。默认原生 `imagegen` 的选择与授权见 [图片后端](image-backends.md)。程序测试不能证明真实图质量、全目录实现或固定分钟数的提速。
