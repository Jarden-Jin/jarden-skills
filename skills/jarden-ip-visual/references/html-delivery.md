# HTML 交付与工作台

每个用户可见轮次都通过 `present_round.py` 交付。页面、manifest 和图片快照一起保存；聊天只给一句变化、页面入口和一个待做动作。

## 生成与呈现

```text
<python> <skill>/scripts/present_round.py <项目>
<python> <skill>/scripts/present_round.py <项目> --no-open
<python> <skill>/scripts/build_gallery.py <项目>
<python> <skill>/scripts/build_gallery.py <项目> --delivery D0004 --out <目录>/review.html
```

- `present_round.py` 生成单调递增的 `D0001…`，把当时图片复制到 `artifacts/sha256/`，写不可变 `deliveries/Dxxxx/manifest.json` 与 `index.html`，再更新项目根 `gallery.html`。
- 没有当前 round 时仍生成交付页：`round_id` 为 `null`，`kind` 为 `summary`，`items` 为空，问题来自 `next_action`。页面应显示唯一缺失问题、推荐值、选项和已保存答案。
- `build_gallery.py` 默认渲染 `presentations.latest_delivery_id`；它不改项目、不制造交付，可用于移动后的项目、只读检查和指定历史交付。
- `schema_version: 1` 的项目只能只读展示。未经明确迁移，不写 `project.json`、不生成新交付。
- 两个 CLI 从读取项目到写完页面、打开回执和输出结果，始终持有真实安装目录的 `execution_lease`；期间更新器不得切换代码。有未结束的安装事务时，命令在写入项目前退出，待恢复后重试。

交付 manifest 是呈现时的冻结记录。用户提交后不反写旧 manifest；回执单独登记，页面刷新时按 `delivery_id` 读取。

真实上次交付与比较基线分别保存。基线可以来自旧交付的顶层图片，也可以来自 `summary` 当时冻结的 `view.history`、`view.picks`；每项记录原轮次、图位、快照位置与哈希，只验证内容寻址对象，不回读当前源图替代旧图。明确 `based-on` 的旧方向与本轮全部 alternatives 对照；set 只比较对应图位。缺少可核实快照时明确不比较，不伪造过去交付。

## 打开回执

只允许四种状态：

| 状态 | 含义 |
| --- | --- |
| `open_dispatched` | 已用系统默认方式派发打开，但没有看到页面 |
| `opened_verified` | 宿主实际核对过 `delivery_id`，且页面图片成功解码 |
| `unavailable` | 没有图形界面或可用打开方式，HTML 路径仍保留 |
| `open_failed` | 派发失败，保留错误与页面路径 |

CLI 和 `present_round.py` 只能记录 `open_dispatched`。`opened_verified` 必须由能看到页面的宿主提供证据；只用 `open` / `xdg-open` 成功返回不能升级成已验证。`build_gallery.py` 不写打开回执。

## 本机交互与静态回退

本机服务只监听 `127.0.0.1`。URL fragment 带 `token`，页面从 fragment 读取，不写入存储或 manifest。

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

## 交付检查

```text
<python> -m unittest discover -s tests -p 'test_deliveries.py' -v
```

至少核对：旧交付在源图被替换后仍不变；最近交付和比较基线来自不同真实 manifest；移动整个项目后图片仍可见；schema 1 字节不变；展示和打开不增加业务 revision；静态页不发 API 请求。
