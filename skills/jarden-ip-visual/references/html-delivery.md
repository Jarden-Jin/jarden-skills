# HTML 交付与工作台

每个新的用户可见轮次都通过 `present_round.py` 冻结交付；恢复已有项目可用 `workbench.py` 复用交付和回执。页面、manifest 和图片快照一起保存；聊天只给一句变化、页面入口和一个待做动作。

## 生成与呈现

```text
<python> <skill>/scripts/present_round.py <项目>
<python> <skill>/scripts/present_round.py <项目> --no-open
<python> <skill>/scripts/build_gallery.py <项目>
<python> <skill>/scripts/build_gallery.py <项目> --delivery D0004 --out <目录>/review.html
```

- `present_round.py` 生成单调递增的 `D0001…`，把当时图片复制到 `artifacts/sha256/`，写不可变 `deliveries/Dxxxx/manifest.json` 与 `index.html`，再更新项目根 `gallery.html`。
- 没有当前 round 时仍生成交付页：`round_id` 为 `null`，`items` 为空；有发帖包时 kind 为 post，否则为 summary。问题来自 `next_action`，展示唯一缺项和已保存答案；有推荐值和选项才显示，用途、尺寸可自由输入。
- `build_gallery.py` 默认渲染 `presentations.latest_delivery_id`；它不改项目、不制造交付，可用于移动后的项目、只读检查和指定历史交付。
- 项目代码读 schema `[1, 2, 3]`、只写 `[3]`。schema 1/2 只能用 build_gallery 或 workbench 只读展示，不写 project.json、不创建交付；present_round 拒绝旧 schema，须先显式迁移。交付 manifest 的 schema_version 仍为 1。
- 两个 CLI 从读取项目到写完页面、打开回执和输出结果，始终持有真实安装目录的 `execution_lease`；期间更新器不得切换代码。有未结束的安装事务时，命令在写入项目前退出，待恢复后重试。

交付 manifest 是呈现时的冻结记录。用户提交后不反写旧 manifest；回执单独登记，页面刷新时按 `delivery_id` 读取。

真实上次交付与比较基线分别保存。基线可以来自旧交付的顶层图片，也可以来自 `summary` 当时冻结的 `view.history`、`view.picks`；每项记录原轮次、图位、快照位置与哈希，只验证内容寻址对象，不回读当前源图替代旧图。明确 `based-on` 的旧方向与本轮全部 alternatives 对照；set 只比较对应图位。缺少可核实快照时明确不比较，不伪造过去交付。

## workbench 恢复入口

```text
<python> <skill>/scripts/workbench.py <项目> --serve
<python> <skill>/scripts/workbench.py <项目> --serve --no-open
<python> <skill>/scripts/workbench.py <项目> --no-open --record-ready
```

`--serve` 请求受控本机服务，不传时只给静态页；`--no-open` 不派发系统打开。`--record-ready` 仅当项目 next=record-round 时检查并登记已有图片，不负责生成、审查、选择或打包。CLI 无任意自动推进或自动生图参数。

workbench 用 `.workbench/presentation.json` 比较业务版本、项目 fingerprint、源文件/round.json/审查摘要、实际 next、最近 manifest/index.html 与快照完整性。全部一致才 `presentation.reused=true` 并重建当前 gallery；否则调用 present_round 创建新 Dxxxx。直接反复调用 present_round 会创建新交付，不具备这项复用判断。

服务记录在 `.workbench/server.json`，按真实项目、project_id、PID、URL、token 与 `/api/state` 健康响应核对；已有健康服务才 `server.reused=true`，否则受锁保护启动 serve_gallery。当前就绪等待预算为 8 秒，单次健康请求预算 1.5 秒，启动的服务时限为 1800 秒；冷启动或较慢机器不因过短探测反复启停。不可用时有界返回静态入口与“新选择尚未提交”，不假装交互模式成功。旧 schema 不启动可写服务。

返回 JSON 包含 presentation、delivery_id、gallery、url、server、open、interaction、next/project_next、committed_receipt/receipts、generation_tasks。最后两项生成控制标记 automatic_generation 和 should_call 始终 false，工作台不会代宿主调用原生图片工具。

恢复 requesting/unknown 或 output_invalid 任务，且项目原 next=generate 时，返回的 next 改为 reconcile-task，原动作保留在 project_next；先核对真实工具回执与文件。此覆盖目前只在 workbench 返回值中，尚未投影到冻结 HTML 的 next；页面仍可能显示“待制作”，Agent 必须以 ledger 和 workbench 结果阻止重复调用。生成任务和图片验收的细节见 [项目状态](project-state.md)。

## 打开回执

只允许四种状态：

| 状态 | 含义 |
| --- | --- |
| `open_dispatched` | 已用系统默认方式派发打开，但没有看到页面 |
| `opened_verified` | 宿主实际核对过 `delivery_id`，且页面图片成功解码 |
| `unavailable` | 没有图形界面或可用打开方式，HTML 路径仍保留 |
| `open_failed` | 派发失败，保留错误与页面路径 |

CLI 的系统打开成功只能记录 open_dispatched；失败或未请求时按实际返回 unavailable/open_failed。opened_verified 必须由能看到页面的宿主调用 `present_round.record_open_result`，提交匹配的 delivery_id、images_decoded=true 和 inspected_by；目前没有独立 CLI 参数可声明已验证。只用 open/xdg-open 成功返回不能升级成已验证，build_gallery 不写打开回执。

## 本机交互与静态回退

本机服务只监听 `127.0.0.1`。URL fragment 带 `token`，页面从 fragment 读取，不写入存储或 manifest。

如需独立启动服务，实际接口为：

```text
<python> <skill>/scripts/serve_gallery.py <项目> --port 0 --ready-file <就绪记录.json>
```

端口 0 由系统分配，使用返回的完整 URL，不猜端口。服务就绪记录与 workbench/server.json 含本机访问 token，应留在受控本机文件，不放进交付快照或分享给外部。服务存活期间持有代码 execution_lease，更新前须先结束服务。

- `GET /api/state`：页面打开后核对项目版本和当前 delivery；核对失败时保留画面、停用提交并保留草稿。
- `POST /api/decision`：只发受控选择请求。浏览器不调用生图、不推进阶段。
- `GET /api/receipt/<request_id>`：真实读回。网络超时后按同一 request ID 查询；只有 `committed: true` 的回执才显示“已采用”。

`file://` 页面不请求任何 API。选择、备注与保持项只进入浏览器草稿；页面明确显示“尚未提交”，并提供可复制的聊天回复。没有服务时不渲染假成功结果。

## 动作与页面状态

工作台调用 `next_action(project, root)` 检查当前源图与实际发布包。历史业务 `stage=done` 不代表当前交付可用；顶部以 `next.action` 为准，`review` 显示待审查、`package` 显示待整理，只有 `done` 显示已完成，不为展示修改业务阶段。

- `answer-field`：只回答当前 `next_action` 的一个字段，另带 `field` 和 `value`。
- `select`：单一方向采用；`approve-set`：逐图保留，未选图位按跳过提交。
- `request-variation`、`request-slots`、`request-redo`：只登记后续意向，不生成图、不推进阶段。
- 缺图、分类审查阻断、物理审查未通过、需求已变化或页面已过期时，对应项必须显示原因并禁用。
- 规划轮缺图只说明尚未生成、已记录的实际阻断原因和提示词是否已填写；没有生成回执不能推断生图能力不可用或声称提示词已保存。
- 提交前显示“尚未提交”；提交失败保留勾选、备注和请求标识；成功后显示读回 revision，并提示回聊天继续。
- 页面支持图片放大、浅底/深底/棋盘透明检查、键盘焦点、草稿恢复；手机端按单列阅读，操作不被底部遮挡。

用途与尺寸问题由 next 中的 `product.usage:<主体ID>`、`product.size:<主体ID>` 驱动，保留自由输入。页面尺寸保存只支持简单 mm/cm 数值转 height 或定性描述；范围、多维尺寸、实测依据等交回 Agent 用 product JSON 保存，见 [项目状态](project-state.md)。不让用户填写 yaw/elevation 等内部机位参数。

已有 committed=true 回执必须读回并续作，不能再执行一次 select；request-* 的 committed 只说明意向已登记，不表示已批准生图。没有宿主事件回调时，用户在页面提交后回聊天说“继续”，再恢复状态。

## 实际图、合同与发布输出

HTML 中的图片和可选择状态来自真实文件及门禁。G0 的 scene_plan/compile 只有计划依据；G1 必须独立看真实图，核对身份、设计尺度和镜头覆盖；G2 由另一位隔离执行者只看匿名图片及品类，检查可见物理。G1 合同、Bible、作者提示词或 G1 判词不得塞进 G2 匿名包，页面文案不能解释掉图像错误。

当前 gallery 已有保持项、bible、分类/物理状态和 G1 阻断后的禁选原因，但尚未单独投影完整 product_contract、scene_plan 或逐项 G1 证据。不能声称页面已具备完整合同编辑器、多视图旋转器或 G1 明细面板；必要说明由 Agent 根据实际 show/审查结果补充，保留审查文件入口。

package_post 的 `--prepare-review` 返回 reviews/post 下的实际排版图片、manifest 和 ordered_slots_file。对返回的完整 images 顺序做 G1 publication，对无有效报告的新字节做 G2；prepare 本身不产生 post 定稿。正式打包优先复制 render_signature 与输入摘要一致的 prepared/已有 post 字节，重新验证图位、合同和实际顺序。只改正文可复用图；改源图、裁切、overlay、实际使用字体或渲染实现产生新像素时须重审。页面交付最终 post 的实际编号图片，不用原场景源图冒充已审排版图。

## 检查方式与证据边界

以下是开发验证命令，会使用临时项目，不属于只读展示；均在 Skill 目录运行：

```text
<python> -m unittest discover -s tests -p 'test_deliveries.py' -v
<python> -m unittest discover -s tests -p 'test_workbench.py' -v
```

检查旧快照不随源图变化、基线来源、移动项目、schema 1/2 只读、业务 revision 不因展示递增、静态页不发 API、回执幂等、工作台/服务复用和未决任务恢复。测试用例或脚本存在不证明真实浏览器、独立看图或某个平台已实测；opened_verified、线上安装与发布必须分别有实际证据。
