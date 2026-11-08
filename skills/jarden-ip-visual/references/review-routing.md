# 分类审查、报告范围与阻断

审查不是把主观分数和客观文件检查混成一个“总通过”。每类报告回答不同问题，绑定自己的实际对象；用户选定不能豁免，某类通过也不能覆盖另一类失败。物理审查始终按 [零上下文物理审查](physics-review.md) 执行。

## 类别和停止点

| 类别 | 回答的问题 | 建议执行者 | 建议范围 | 失败或缺失时 |
| --- | --- | --- | --- | --- |
| `files` | 图片存在、可解码、摘要、图位、透明与交付格式 | 即时程序检查 | 当前明确传入的图片 | 不要求独立报告；实际客观失败阻断；缺图留档不等于通过选择 |
| `identity` | 轮廓、五官、色区、配件、角色数量和系列规则是否保持 | 每批并排自检；概念定稿、系列首只和关键返修做独立样本检查 | 当前批次的全部目标图 | 明确违反已确认身份时阻断该项；缺失标“身份未评” |
| `creative` | 首轮方向是否有实质差异，材质与任务目的是否成立 | 首轮代表方案一次独立创作评审，返修按影响复核 | 代表方案，不要求每张重复审计 | 问题返修；缺失标“创作未评”，不写全审通过 |
| `physics` | 重力、支撑、连接、接触、光影是否可信 | 原有零上下文独立 Agent | 每张适用场景、编辑、合成和最终输出 | 仅 `pass` 可推进；缺失、`uncertain`、失败都阻断 |
| `sequence` | 图位与顺序、封面识别、差异、生活场景、信息增量、传播素材是否成立 | 即时程序查缺位；可选独立 Agent 看整组 | 整套定稿或待发布图位 | 缺图位、不可读文件或有效失败报告阻断；缺主观报告标未评 |
| `copy` | 标题、正文、tag 与图、已确认事实是否对应 | 即时复用打包器检查预算与格式；可选独立事实核对 | `copy.json` 与实际发布图 | 程序错误或有效失败报告阻断；程序通过不代表事实核对通过 |
| `ux` | 页面能否比较、选择、解释禁用和恢复 | 页面走查者 | 真实页面当前模板版本 | 阻断选择或恢复的缺陷阻断发版 |

“独立关键样本检查”只约束身份、首轮创作和整套传播素材，不把每个主观类别扩张成每张图的新调用。物理审查的匿名包不加入 Bible、创作解释、作者偏好或历史通过。

`files/copy/sequence` 的程序 `pass` 只表示本次客观检查通过，不写独立报告、不宣称主观验收完成；`review=not_assessed` 和 `not_assessed=true` 保留该区别。`files` 可选登记客观审查覆盖记录，但记录不能覆盖真实解码或透明检查。每类笔记的建议 tag 数量是建议，不升级成阻断所有项目的硬规定；标题/正文/tag 格式继续复用打包器的单一规则。

解码、非空和可读性检查适用于所有阶段；3:4 仅检查 `package/done` 传入的实际发布图，概念、材质、透明资产和场景原图可用其他画幅。`cutout` 复用真 alpha 检查；不合格允许留档供返修，选择资格仍由项目核心透明检查阻断。`record-round --allow-missing` 传入 `paths=[]` 时不杜撰文件或物理通过，仍允许缺图登记；任何实际存在的场景图缺物理报告仍强阻断。

UX 用于作者发版验收，可单独 `status(..., "ux")`；普通客户的 `record/select/final` 不因 UX 缺审或作者走查报告阻断。

## 报告格式

`schema_version` 为 2。最小结构如下：

```json
{
  "schema_version": 2,
  "review_id": "review-…",
  "category": "identity",
  "scope": {},
  "image_hashes": ["逐张原图 sha256"],
  "decision_signature": "当前确认决策摘要，非身份类可为 null",
  "template_version": null,
  "reviewer": {
    "id": "实际独立执行者标识",
    "independence": "zero-context",
    "viewed_images": true
  },
  "verdict": "pass",
  "findings": [],
  "evidence": "本次实际检查的整体可见证据",
  "paths": [
    {
      "path": "rounds/…/A.png",
      "sha256": "原图摘要",
      "verdict": "pass",
      "evidence": "这一张的可见依据"
    }
  ],
  "criteria": {}
}
```

- `verdict` 只能是 `pass`、`needs_revision`、`uncertain`。`uncertain` 不是通过，也不是待补可通过。
- `findings` 只能为空（`pass`）或逐条含 `code`、`severity`（`blocking`/`advisory`）、`detail`、`evidence`、`fix`。
- `paths` 必须逐张覆盖请求范围，且带实际原图摘要；不能只写文件名、缩略图或“平均值”。
- `creative` 必须覆盖 `distinct_directions`、`material_credibility`、`purpose_fit`、`identity_continuity`、`propagation`。
- `sequence` 必须覆盖封面识别、实质差异、生活场景、信息增量：`cover_recognition`、`meaningful_difference`、`life_scene_fit`、`information_gain`。它不声称已审当前文案。
- `copy` 必须覆盖 `verifiable_copy`、`relevant_tags`、`copy_image_match`。含数字、规格、价格、授权、打样、发售、销量等事实主张时，`facts` 必须逐项写明 `verified`、`unknown` 或 `contradicted` 及依据。只要不是 `verified` 就不能整体 `pass`。程序对事实词的识别只是补漏提示，不证明已列全全部语义主张；执行者仍须核对完整标题、正文和 tag。
- `ux` 必须覆盖 `selectable`、`comparison`、`disabled_reason`、`recovery`、`mobile_keyboard`，声明 `reviewer.viewed_page=true`，并绑定实际使用的模板版本。审查者需在 `evidence` 中写明实际页面、操作与观察；程序不把这一声明当作真实浏览器取证。

报告记录在 `reviews/categories/<category>/<review_id>.json`。请求、请求摘要和封存指纹在对应 `requests/<review_id>/`；同一请求不能改字节重放批准。程序只证明格式、范围、摘要和声明，不证明审查者真的隔离、看过图或主观判断正确。

## 失效边界

报告只对检查过的对象负责，不做项目级“一刀切”：

| 变化 | 只使什么失效 |
| --- | --- |
| 图片字节变化、换路径 | 绑定该路径/摘要组合的类别报告范围；不涉及此图的报告不受影响；物理审查按原流程失效 |
| 输入参考或已选概念/材质/透明图的参考集合与字节变化 | 绑定这些参考的 `identity/creative`；不影响文案或 UX |
| `brief.decisions` 或有效 Bible 身份参考变化 | 只影响 `identity`；来源、scope、evidence 都计入身份签名；其他类别不自动失效 |
| `copy.json` 字节变化 | `copy`；图序和物理报告仍在各自范围内 |
| `finals` 图序、图位或主体对应变化 | `sequence`；原图未换字节不影响物理报告 |
| `assets/gallery.html/css/js` 变化 | 只影响 `ux` 模板报告 |
| 产品形态变化 | 物理审查按原 `physics_review` 产品上下文失效；不得复用匿名包 |
| 图序或文案未变但无关轮次增加 | 不使既有报告失效 |

报告绑定的是“这一组原图 + 这一组适用签名”。类别报告目前同时绑定原路径与摘要，换路径需新请求；同份范围内任一图片变化后，该份报告需复核，不会扩大到别份不相关报告。物理报告依原协议可按相同字节复用。不能借换文件名扩大范围或掩盖旧范围里的失败。过期报告显示 `not_assessed`，`stale` 列出 `review_id` 与 `images_changed/decision_changed/copy_changed/sequence_changed/references_changed/template_changed` 等原因，不能继续显示通过；已登记报告被删除或改字节则是完整性错误，而不是“可选未评”。同范围较新报告可以修复旧问题，旧请求不能重放覆盖更新判定。

## 决策来源

身份和系列约定来自 `brief.decisions` 的 `{id, rule, scope, origin, evidence}`：

- `origin=user-confirmed`：用户明确说过的规则，可作为身份对照。
- `origin=agent-inferred`：Agent 的推断，不能冒充确认；报告中指出冲突时必须能追溯来源。
- `origin=reference-derived`：从原图或参考继承，不等于用户已批准所有延展。

创作审查包可带经筛选的 Bible 参考、确认规则和候选图，但不带作者提示词、作者希望选哪张、历史好评或预设结论。身份和创作包还提供输入图、已选概念/材质/透明图的查看副本，并标明 `reference-derived/user-confirmed`，不提供挑选理由。包内 PNG 清除作者元数据；候选与参考都绑定项目原图摘要，不用查看副本的摘要替代原图。Bible 筛选只是有限的字段/文字防泄漏措施，不能证明任意自由文本完全中性；派发前仍须检查。其他复杂决策按实际语义判断，不为少数案例硬编码新的来源类型。

## 四类整套素材的审查重点

只借鉴图的功能和顺序，不复制参考帖的角色、品牌、水印、署名、销量或未证实事实。样本中的互动差异是观察，不是转化因果证据；旧分析与当前规则有差异时，按用户当前要求、确认约定和 [文案规范](copywriting.md) 判断。

| 笔记类型 | 识别与信息增量 | 文案与 tag 核对 |
| --- | --- | --- |
| [同系列合集](note-types/series-collection.md) | 全家福可数、单只同比例可比较、每只保留独立身份、使用场景交代尺度；不强制固定四只 | 列全角色名与真实进度；创作大词→题材→工艺/方法→情绪→形态/人群，通常 8–10 个 |
| [IP 设计手稿](note-types/design-manuscript.md) | 成品封面→设定/三视图→真实过程或明确标注的本轮 AI 设定→人设场景→延展；两张相近渲染不冒充手绘或工厂过程 | 讲真实设计决定与当前阶段，不承诺工程打样；相关活动→原创/IP→品类→实际工具→情绪/人群→IP 名，通常 8–10 个 |
| [单个玩偶多维展示](note-types/single-multiview.md) | 同一主角认脸、尺度、材质、角度、新用法、包装/信息各有作用，不用换包背景反复凑张数 | 图中可见的设计与用法，未知尺寸/价格待定；已有品牌→产品→题材→品类→情绪，通常 3–7 个 |
| [多个玩偶多场景](note-types/multi-scene.md) | 全员清晰可数、第二张关系/角度有变化、居家/主题/出门功能不同，群像与单只一致 | 名称、数量、关系与图相符，不伪造开箱或口碑；已有品牌→品类→原创→情绪→用途人群→相关活动，通常 6–10 个 |

传播力只评估六项可见对象：封面识别、实质差异、生活场景代入、信息增量、真实可验证文案、相关且有序的 tag。AI 场景的自然光影不能证明是真实摄影；没有用户资料或实物证据，不写已打样、已授权、已量产、已上市或保证流量/销量。默认干净图片，AI/未知规格说明放正文、HTML 或平台声明，不从参考帖继承图字。

## CLI 与主 Agent 接线

准备和登记独立报告：

```text
<python> <skill>/scripts/review_checks.py prepare <项目> --category identity --producer <制图执行者> --images <图…> --stage concept
<python> <skill>/scripts/review_checks.py record <项目> --category identity --request <review_id> --report <报告.json>
<python> <skill>/scripts/review_checks.py status <项目> --category physics --images <图…>
<python> <skill>/scripts/review_checks.py check <项目> --stage package --images <待发布图…>
```

Python 主入口：

```python
import review_checks

state = review_checks.status(root, project, "identity", paths=["rounds/r1/A.png"])
gate = review_checks.blocking_findings(root, project, paths, stage="package")
```

`status(root, project, category, paths=None)`：

- 返回该类的 `verdict`、逐图 `paths`、覆盖数、缺失/失效原因和绑定摘要。
- 不创建报告、不补写通过、不调用生图。
- `identity/creative/ux` 缺失或范围变化返回 `not_assessed`，不阻断普通客户流程；有效失败报告仍阻断身份或创作对象。`creative` 的覆盖代表所送代表方案，不宣称未送图片也已独立看过；逐图 `coverage` 保留实际范围。
- `files/copy/sequence` 返回即时程序检查，并以 `review` 单独表示是否有有效独立记录；物理缺审仍为 `missing` 且阻断。

`prepare` 和 `status` 都是只读业务状态：不增加项目 `revision`，不修改页面、选择、图片或物理盲审包。CLI `prepare` 在项目锁内通过 `state.load_project(root, writable=True)` 检查可写 schema，只新增自己的审查请求包和请求摘要，不调用 `save_project`；schema 1 需先迁移再生成新包。

`blocking_findings(root, project, paths, stage)`：

- 先看 `blocking`。为真时不得 `record-round`、`select`、`final`、打包或推进。
- `findings` 是客观缺陷或有效失败报告；其中 `severity=advisory` 仅是返修提示。以顶层 `blocking` 为门禁，不能以“findings 非空”代替。
- `not_assessed` 是缺失/过期的可选独立报告，表示“未评”，不表示通过；已有失败报告显示 `review=blocked`，不会误写成“只是未评”。`categories` 只列必要物理与有问题的类别；即时文件检查放 `inspections.files`。
- 物理缺审、失败和不确定始终进入 `findings`；创作总分或身份通过不能覆盖它。
- 省略 `paths` 时，`package/done` 只检查实际定稿图；其他阶段默认项目图片。调用方应传本次真实范围；显式空列表仅用于缺图登记，不代表已有图片已审或发布集合完整。

`record` 是业务写入。它在 `project.project_lock(root)` 内重新执行 `state.load_project(root, writable=True)`，复验请求摘要、报告范围、当前图片/文案/模板签名，然后通过 `state.save_project` 写入 `review_records` 并增加一次 `revision`。相同报告的幂等重试返回 `unchanged=true`，不再增加 revision。旧页面看到 revision 变化后应重新读取，不能拿旧资格继续提交。

全部 CLI 动作都包在 `runtime_activity.execution_lease()` 内，避免更新与审查记录混用。

`physics` 在本模块仅允许只读 `status/check`，`prepare/record` 明确拒绝并指向原 `physics_review.py`，不改写匿名包。

## 保守判定

- 明确违规、有效报告的 `needs_revision/uncertain` 或当前已登记报告的完整性错误，阻断相关对象。仅改同批其他图片不能清除未改图片的负面结论；当前图片或约定变化使相关旧通过失效，不能沿用。
- 审查按内容摘要绑定实际图片；改临时目录、另存同字节或重新排版得到同字节，不能绕开已有负面结论。新独立报告只替换相同当前对象的旧判定，不清除其他未修改对象的问题。
- 打包使用 `--copy` 时，文案的程序检查与独立报告范围都绑定这个实际文件内容，不读取默认文案冒充已审。对应 `prepare/status/check` 也可传 `--copy`；不传时才用项目里的 `copy.json`。
- 缺失的 `identity`、`creative`、`ux` 报告记为未评，不伪造“全部已审查”；需要身份门槛的调用方可把身份未评升级为阻断。
- 传播素材只审查可见的封面识别、图间差异、生活场景代入、信息增量、真实可验证文案与相关 tag。任何审查结论都不保证流量、销量或平台推荐。
- 物理审查没有独立看图能力时停在待审；不得以用户选定、作者自检、报告平均分或整套文案通过替代。
