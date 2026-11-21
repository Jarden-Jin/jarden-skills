# jarden-skills

Jarden 的可复用 Skill 集合，用于把创作与交付流程整理成可重复使用的工具。

## Skills

| Skill | 用途 |
| --- | --- |
| [jarden-ip-visual](skills/jarden-ip-visual/README.md) | 从四组 21 项目录按需制作原创 IP 设计与宣发资产，用常驻 Archify 工作台跟踪真实状态；可选将确认成图延展为 Live Photo，分别记录配对、图库播放和设备验收，不强制商品或发帖流水线。 |
| [jarden-publisher](skills/jarden-publisher/README.md) | 将定稿图片与文案准备成小红书图文、抖音图文或 B 站专栏，集中预检账号、封面和发布设置，存稿后回读，默认停在发布前。 |
| [jarden-image](skills/jarden-image/README.md) | 通过 Jarden 的远程 API 调用 MOSS 或专用 H3 Image，查询参数、估时、恢复任务并校验下载图片；不包含模型和任何 API Key。 |
| [jarden-video](skills/jarden-video/README.md) | 通过 Jarden 的远程 API 生成 H3 文生视频或首帧视频，选择服务支持的采样配置，恢复任务并校验下载；不包含模型和任何 API Key。 |

## 安装

告诉支持 Skill 的 Agent：

```text
请帮我安装这个 Skill：https://github.com/Jarden-Jin/jarden-skills/tree/main/skills/jarden-ip-visual

请帮我安装这个 Skill：https://github.com/Jarden-Jin/jarden-skills/tree/main/skills/jarden-publisher

请帮我安装这个 Skill：https://github.com/Jarden-Jin/jarden-skills/tree/main/skills/jarden-image

请帮我安装这个 Skill：https://github.com/Jarden-Jin/jarden-skills/tree/main/skills/jarden-video
```

也可以使用命令安装：

```sh
npx skills add Jarden-Jin/jarden-skills --skill jarden-ip-visual
npx skills add Jarden-Jin/jarden-skills --skill jarden-publisher
npx skills add Jarden-Jin/jarden-skills --skill jarden-image
npx skills add Jarden-Jin/jarden-skills --skill jarden-video
```

本仓库公开，安装不需要私有仓库读取权限。命令安装需要 Node.js；它不是 Skill 核心流程的运行依赖。公开安装不代表获得生成服务权限，`jarden-image` 和 `jarden-video` 的调用者必须自行提供获授权的个人或设备 API Key。

## 使用与依赖

安装后可以直接说：

```text
用 jarden-ip-visual 把这张原创草图做成毛绒挂件，要一套小红书素材。

用 jarden-ip-visual 做这只原创角色，本轮只要 02 线稿和 04 平涂，不做场景和发帖。
```

`jarden-ip-visual` 需要 Python 3.10+，图片处理需要 Pillow，完整流程还需要生图能力，以及能够独立看图且不继承创作对话的隔离 Agent。使用宿主已有生图能力时无需额外配置 Key。macOS 已验证，Windows/Linux 尚未实机验证。

默认交付干净无提示字的图片。场景图和实际发布图必须通过独立物理合理性审查；这属于视觉检查，不是数值仿真或工程证明。

目录不代表全部能力已实现：06 真实可编辑光影分层源文件仍待支持，13—15 只作有来源的设计参考，不是制造文件。源码安装与 Release 更新独立，源码版本号不代表已有同号 Release。

页面确认经本机受控服务写回项目并返回回执；静态页面只保存草稿，不冒充已经采用。更新有独立计划、校验与备份，不会自动迁移用户项目。

配置、数据接收方、费用边界、测试和恢复方法见 [jarden-ip-visual 使用说明](skills/jarden-ip-visual/README.md)。项目和成果默认留在本地；调用图片服务时会发送提示词与指定参考图。该 Skill 只交付发帖素材，不操作创作中心。

已有定稿时，可以说「用 jarden-publisher 把这组图片和文案准备成小红书和抖音草稿，停在发布前」。浏览器登录、预检和发布边界见 [jarden-publisher 使用说明](skills/jarden-publisher/README.md)。

## 远程图片和视频 API

`jarden-image` 和 `jarden-video` 使用 Python 3.12+ 标准库客户端，电脑无需安装模型、CUDA、ComfyUI 或 VPN。默认服务为 `https://ai.comark.work`，可用 `--base-url` 修改。

- 两个公开 Skill 只带说明、客户端和界面元数据，不包含个人 Key、设备 Key、迁移备份或本机账户文件。
- 首次调用由使用者自行隐藏输入个人 Key，或提供私密 Key 文件。校验成功后，客户端把凭据保存到 Skill 目录以外的用户配置目录，并创建或复用本机设备 Key。
- 凭据缓存为受本机文件权限保护的明文文件，不是系统钥匙串。可以使用 `--token-file` 直接读取已有设备 Key，绕过个人 Key 缓存和设备注册。
- 提示词、生成参数和指定上传图片发送给配置的 API；生成结果下载到本地。访问权限、费用、限额及模型许可由服务方决定，安装本 Skill 不会自动提供服务额度。
- macOS 已验证真实图片和视频生成；本次发布不宣称已完成 Windows/Linux 实机验证。

安装后可以说“用 jarden-image 生成一张写实边牧照片”或“用 jarden-video 生成蛇从暗处爬出来的写实视频”。完整配置和恢复方法见各自 README。

离线回归与公开文件检查：

```sh
python3 tools/check_public_api_skills.py
python3 -m unittest discover -s tests -p 'test_public_api_skills.py' -v
```

这些检查使用虚拟凭据和模拟请求，不会发起真实生成，也不需要任何线上 Key。

## 许可证

当前未附带开源许可证。
