# Jarden 创作工作台 · 纯白工作室

## 方向与范围
- 用户确认：01「纯白工作室」，沿用石白画布、浅色侧栏、松绿强调；只重塑工作台 UI，不更改生成供应商与业务规则。
- 用户确认：真实项目与 current 数据驱动身份、资产、图位和状态，不固化原型角色、数量或预选方向。
- Agent 推断：使用项目原生 HTML、CSS、JavaScript，不安装依赖；沿用按钮、输入、details 与 dialog。

## 工程来源
- Agent 推断：模板为 `skills/jarden-ip-visual/assets/gallery.html`，样式为 `skills/jarden-ip-visual/assets/gallery.css`，行为为 `skills/jarden-ip-visual/assets/gallery.js`。
- 用户确认：样式与交互是原组件的变体；项目导航、资产目录、轻量决定栏为新组合，不整段搬运原型。

## 视觉变量
- 用户确认：`--canvas` 是石白画布，`--surface` 是内容与浮层，`--navigation` 是浅侧栏；不使用大面积阴影或高饱和背景。
- 用户确认：`--ink` 用于主内容，`--muted` 用于仍需可读的辅助信息，`--line` 仅表示实际边界。
- 用户确认：`--accent` 与 `--soft` 共用于当前导航、图片选择、保持项和主动作；警告与失败分别用 `--warning`、`--danger`。
- Agent 推断：系统中文无衬线字体，正文 14px，标题 23–27px，辅助文案 12px，数字采用 tabular-nums。
- Agent 推断：`--radius` 统一为 7px；`--control-height` 为 44px；`--duration` 为 160ms，减少动态效果偏好下关闭过渡。

## 布局与组件
- 用户确认：桌面以项目侧栏、紧凑顶栏、双图判断区、右侧轻量决定构成；图片始终 contain，不裁切原图。
- Agent 推断：`--nav-width` 为 188px，`--inspector-width` 为 232px；1040px 以下决定栏移至图片之后，700px 以下导航改为顶部按钮组。
- 用户确认：390px 窄屏保留双图比较；长标题换行、目录单列、表单字体避免移动浏览器自动放大。
- Agent 推断：窄屏资产目录提供“查看已选项与保存方式”入口，跳至决定栏，不迫使用户滚过全部目录才能找到保存。
- 用户确认：产品约定、G0、G1、提示词、源文件摘要默认折叠；不可采用原因仍直接可见，不隐藏关键阻断。
- 用户确认：侧栏与右栏资产名称、数量、当前项从同一目录及 selected 推导，不维护第二份业务目录。

## 选择与保存
- 用户确认：资产目录完整展现服务器下发的 01–21 项，按 category 分组多选；support 不支持或未知时不可新选，不伪造分层源文件或制造能力。
- Agent 推断：asset_workflow 可缺字段；catalog 与 instances 兼容数组或键值映射，current.items 同时兼容数组或键值映射。
- Agent 推断：分组名称优先 name，其次 label；候选展示编号优先 label、badge、candidate，业务提交仍使用真实 instance ID。
- 用户确认：document 文件不进入 img 解码；提供原文件链接与证据，不能因浏览器不能显示 Markdown 而误标文件缺失。
- 用户确认：服务端未显式声明支持的资产动作只提供回复草稿，导出不表示已保存、已生成或已采用。
- 用户确认：token、expected_revision、交付编号、图片 SHA-256、幂等 request_id 与真实 receipt 读回继续使用；服务端承担最终资格校验。
- 用户确认：stale、readonly、未核对实时状态、已经提交均禁止业务写入；静态快照只留草稿，失败不清空输入。
- 用户确认：intent_selectable 只允许登记方向意向，不等于 selectable；物理待审图片不可因意向被标为正式采用。
- Agent 推断：草稿按项目和交付隔离，恢复选择、资产清单、备注、保持项、当前页面与待恢复请求。

## 键盘与反馈
- 用户确认：原生 radio、checkbox、details 和 dialog 保留键盘语义；可见焦点独立于业务选中态。
- 用户确认：放大、导出和维护请求均使用原生 dialog；Escape 与遮罩关闭，关闭后恢复触发焦点，重复打开不积累旧弹窗。
- Agent 推断：只使用安静的边框与底色过渡，提交中禁用冲突动作；成功仅在 committed 回执读回后显示。

## 常驻工作状态
- 用户确认：沿用纯白工作室，用 Archify 工作路线常驻表达编辑者当前阶段、下一步和阻塞；不再用所浏览页签推断业务进度。
- Agent 推断：顶部固定区域由紧凑身份栏、当前工作摘要和横向流程图组成；滚动与切换内容时保留。手机只保留摘要，通过工作详情查看六个阶段。
- Agent 推断：流程图素材为 `skills/jarden-ip-visual/assets/workflow-map.svg`，来自校验过的 Archify 原生工作流；只表达路线，不强制每次完整经历六阶段。
- Agent 推断：当前、已有完成记录、待推进、待处理同时使用文字与颜色表达；只有真实正在请求的任务才显示执行动效。
- Agent 推断：`workflow_status` 来自项目与任务记录，与不可变交付画面分离；输入和弹窗期间更新状态区，保留编辑控件、焦点与滚动。
- Agent 推断：点击流程节点打开只读详情，包含任务、阻塞及最近记录；连接中断明确显示上次状态，静态页不声称实时。

## 前端接线
- Agent 推断：服务端可在 asset_workflow.submit_actions 数组声明已接通的动作；未声明时不发送资产写入请求。
- Agent 推断：choose-assets 附 asset_ids；approve-asset 附 asset_id 与 instance_id；asset-intent 另附 intent，继续携带公共 items、note、keep 与并发字段。
- Agent 推断：choose-assets 与 approve-asset 的 evidence 记录用户本次点击、交付和所选编号；资产意向要求说明修改内容，不授予生成权限。
- 用户确认：legacy 缺少 asset_workflow 时保持 select、approve-set、answer-field 与 request-* 原协议，不隐式迁移旧项目。
