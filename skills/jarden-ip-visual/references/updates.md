# 受控更新与作者发行

本文件对应 `scripts/check_update.py`、`scripts/update_runtime.py` 与仓库的 `tools/build_release.py`。更新只处理 Skill 代码，不扫描、迁移或回滚用户项目，也不修改图片服务设置与凭据。

## 可信来源与版本

固定来源为 [Jarden-Jin/jarden-skills](https://github.com/Jarden-Jin/jarden-skills)，Skill 路径固定为 `skills/jarden-ip-visual`，channel 为 `stable`。仓库为私有；更新器不改变可见性。正式标签格式为 `jarden-ip-visual/v<MAJOR.MINOR.PATCH>`，例如 `jarden-ip-visual/v0.2.0`；示例不代表远端标签或 Release 已存在。

每个 Release 的资产必须是 `release.json` 和 `jarden-ip-visual-<version>.zip`。索引记录固定来源、版本、完整 commit、归档大小与 SHA-256、逐文件路径/大小/摘要/权限、项目 schema、依赖、变更类型及固定 commit 的迁移说明。ZIP 只包含 `jarden-ip-visual/` 前缀的清单文件，不包含外部 `release.json`，因此归档摘要与索引不自引用。`evals/`、图片凭据、普通设置、安装回执与缓存不进入发行包或清单。

只接受固定仓库的 HTTPS API 与 Release 下载入口；索引和 ZIP 通过当前 Release 绑定的资产 ID 从官方资产 API 获取，兼容私有仓库的授权下载。二进制资产可由官方接口直接返回或重定向到 GitHub 官方资产主机，跨资产主机不携带 GitHub Authorization；普通 JSON API 不接受重定向。发布说明只当数据，不执行其中的命令，也不接受索引指定的新安装器或新来源。摘要验证完整性，不是独立作者签名；当前没有固定公钥的签名验证。

私有访问复用已有 `gh auth login --hostname github.com` 身份。更新器在内部调用 `gh auth token --hostname github.com`，Token 只留进程内存，不写入 Skill、项目、日志或更新记录；不读取图片 API Key。需要时用户可自行运行 `gh auth status --hostname github.com` 检查身份。未登录或无仓库权限时停止，不能用图片 Key 代替，也没有任意本地包/任意 URL 的安装参数。

## 检查与展示

每个会话首次使用时由 Agent 做一次短时检查；用户关闭检查或要求离线时跳过。HTML 维护区只读取当前安装实例的本机缓存，随交付保存检查时间；渲染页面不联网。无结果、过期或失败均不能显示“已是最新”。页面的检查和计划按钮只生成可复制的维护请求，不调用项目选择 API、不下载或安装；用户回对话发送后由 Agent 执行对应动作。

`<python>` 在 macOS/Linux 通常是 `python3`，Windows 是 `py -3`；要求 Python 3.10+。`<skill>` 是实际安装入口，不是源码 checkout；`<workspace>` 是安装目录外的工作区。

```text
<python> <skill>/scripts/check_update.py status --json
<python> <skill>/scripts/check_update.py check --json
<python> <skill>/scripts/check_update.py check --force --json
```

- `status.version` 是本地 `SKILL.md` 的已安装版本，`status.real_root` 是解析链接后的真实安装目录；另有 `active_readers`、`transactions` 和 `needs_recovery`。`status` 不联网；版本文件损坏时 `version` 可为 `null`，不应伪报正常。
- `check.current` 是已安装版本，`check.latest` 是固定仓库首批最多 100 个 Release 中发现的最高 stable Skill 标签版本，`update_available` 表示后者更高。检查不下载索引或 ZIP；可用标签不等于包已通过安装校验，完整校验发生在 `plan`。
- 成功检查缓存 **24 小时**，失败后 **1 小时**内不自动重试，短时检查网络预算为 **2 秒**。`--force` 只跳过成功缓存与失败冷却，不放宽来源和权限。2 秒不是操作系统 DNS 或进程启动的硬实时保证；慢网络会返回失败，不应阻断创作。
- 检查失败返回非零状态与 JSON 错误；冷却时返回 `deferred: true`、`latest: null`。页面应保留已安装版本并写“暂无法检查”，不能把未知版本显示成“已是最新”。不需要检查时不调用 `check`，没有隐式安装或后台升级。

## 计划与显式安装

以下示例用于已安装版本低于 `0.2.0` 的目录；同版本安装或降级不走 `plan`。

```text
<python> <skill>/scripts/check_update.py plan --version 0.2.0 --out <workspace>/update-plan.json --json
<python> <skill>/scripts/check_update.py apply --plan <workspace>/update-plan.json --yes --json
<python> <skill>/scripts/check_update.py status --json
```

`plan` 只读安装目录，把元数据、下载缓存和计划写在外部；不导入或执行候选版本。索引与计划采用固定 schema，未知字段被拒绝。计划列出当前/目标版本、固定来源、commit、包与索引摘要、真实路径、`add`/`modified`/`remove`、本地前置摘要、兼容范围与外部备份位置。目标已存在时不覆盖计划文件。

先核对真实目录与共享链接影响，再逐项核对 `modified` 和 `remove`：这些是当前目录相对候选发行的差异，不能直接等同于“用户自己改过的文件”。未管理安装也会列出本地文件，批准计划后才备份并执行；项目、配置或私人文件误放在安装目录时，先移出，不批准它们的移除。计划后安装内容变化会拒绝旧计划，须重新生成。源码 checkout 拒绝计划/安装，源码由作者维护。

`apply` 必须显式 `--yes`；已获准的目标无需反复索取同一许可，但不能省掉命令标记。它会重新校验计划、归档摘要、ZIP 路径与文件摘要、包内版本和活动占用。符号链接、非普通文件、路径逃逸及不匹配的包均拒绝。安装后读回版本与逐文件摘要，再运行新版离线 `selfcheck`；只有通过才写安装回执与提交事务。安装是多个文件系统动作，不宣称全程原子。

## 执行租约与实例锁

业务 public API 为 `runtime_activity.execution_lease(skill_root: Path | None = None)` 上下文管理器。省略参数时使用模块所属 Skill；所有入口应让租约覆盖完整业务生命周期，包括页面服务的存活时间。按 `resolve()` 后真实路径隔离实例，共享链接指向同一路径时共用占用记录。

reader 注册 gate 默认最多等待约 2 秒；注册后只留 reader 记录，不长期持 gate 或项目状态锁。退出时清理；已退出进程的记录可回收，无法确认的记录保守拒绝切换。`RuntimeBusyError` 是 `OSError` 子类。安装/恢复/回滚使用同一实例 gate 的 fail-fast 独占锁，不获取 reader lease；有活动生成、审查、打包或服务时返回 `runtime_busy`，应先结束业务，不能自动杀进程。

## 外部记录、备份与恢复

默认更新根目录为：macOS 的用户 `Library/Application Support/jarden-ip-visual/updates`；Windows 的 `LOCALAPPDATA/jarden-ip-visual/updates`；Linux 的 `XDG_STATE_HOME/jarden-ip-visual/updates`（无该变量时使用用户 `.local/state`）。可用 `JARDEN_UPDATE_HOME` 指定；它不得与真实安装目录互相包含。外部工作区应与安装同卷；跨卷切换和 Windows 文件占用尚未实机验证。

- `releases/jarden-ip-visual/<version>.json` 常驻记录首次见到的版本、标签、commit 与归档摘要；后续同号内容变化返回 `version_mutated`。记录按更新根共享，不因另一个安装实例而重置信任；不要删除记录来绕过拒绝。首次获取仍依赖可信仓库，不能发现首次获取之前已发生的替换。
- `instances/<真实路径摘要>/` 保存该实例的 `check.json`、`failures.json`、下载缓存、reader 记录、事务、`backups/` 与 `staging/`。完整旧安装快照位于事务备份的 `source/`，切换的旧文件位于 `active/`；成功后仍保留备份。
- 安装目录的 `.jarden-install.json` 是提交后的代码回执，不是项目状态。原有图片配置与安全凭据仍按原位置读取，更新器不迁移它们。

```text
<python> <skill>/scripts/check_update.py status --json
<python> <skill>/scripts/check_update.py status --recover --yes --json
<python> <skill>/scripts/check_update.py rollback --transaction <事务ID> --yes --json
```

普通安装失败尝试自动恢复旧代码并保留错误证据。进程退出或切换中断后先看 `status`；`needs_recovery: true` 时停止业务，保留计划、缓存、事务与备份，再用 `status --recover --yes`。它处理未结束事务：尚未切换的 `prepared` 中止；完整且校验通过的新代码可完成为 `recovered`，否则恢复旧版；未知 phase 或损坏日志不猜测。重复恢复已结束事务不重复安装。

`rollback` 接受 `committed` 或已恢复验证的 `recovered` 事务，并先核对当前新版版本与摘要；本地代码变化、原移除路径出现新内容时拒绝，不能覆盖升级后新建的工作。切换中断的旧版恢复以完整 `source/` 快照为依据，先验证全部所需备份，再修改安装文件；不依赖尚未搬迁完成的 `active/`。恢复/回滚自身失败时保留 `rollback_failed` 与外部备份，不删除证据；若当前更新入口也受损，停止运行业务，由作者核对外部 `source/` 快照与事务后人工修复。不要把本节理解成任何时点均可自动恢复的保证。

代码回滚不回滚项目。项目 schema 兼容声明为读 `[1, 2]`、写 `[2]`；迁移由项目专用入口另行计划与执行。旧程序必须拒绝写入已迁移的 schema 2，不能为了运行旧代码丢弃新轮次或图片。

## 作者构建与发行

在源码仓库完成检查、固定 `SKILL.md` 的版本、提交并准备对应本地标签后，由作者运行：

```text
<python> -m unittest discover -s skills/jarden-ip-visual/tests -p test_updates.py -v
<python> tools/build_release.py --version 0.2.0 --commit <完整40位commit> --change-type minor --output-dir <外部发行目录>
```

builder 只读 Git blob，不读取未提交改动；要求已有 `jarden-ip-visual/v0.2.0` 标签指向指定 commit，且该 commit 的 `metadata.version` 相同。按固定文件顺序、时间戳和权限生成 ZIP 与外部 `release.json`；目标已存在则拒绝覆盖。`--change-type` 可为 `patch`、`minor`、`major`，命令不会创建提交、标签、Release 或上传资产。

作者应先准备并核对两份资产，再由有权限的发布流程上传到同名 Release；不要先宣布没有包的可用版本，不替换已发版本内容。`check` 从 Release 标签发现可用版本，`plan` 从对应 Release 获取外部索引。只读测试 CI 不发布、不上传、也不证明远端路径已经验证。

## 验证边界

本地测试使用临时安装、临时 Git 仓库与模拟 GitHub HTTP 响应，覆盖真实 CLI 的安装、损坏包、并发、退出恢复、回滚及常驻版本记录。运行证据只代表 macOS；Windows/Linux 仅静态兼容设计，未实机验证。尚未完成真实私有 Release 的“发现 → 授权下载 → 干净安装 → 旧版升级 → 恢复”全链路验证；不能把本地测试或 workflow 文件存在写成真实 GitHub 发布成功。离线可查看 `status`、应用已校验缓存计划与恢复事务；无可信缓存时不能凭空更新。
