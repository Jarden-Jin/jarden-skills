# jarden-skills

Jarden 的可复用 Skill 集合，用于把创作与交付流程整理成可重复使用的工具。

## Skills

| Skill | 用途 |
| --- | --- |
| [jarden-ip-visual](skills/jarden-ip-visual/README.md) | 把原创 IP 草图、灵感图或平面 AI 图，逐步做成毛绒、拼图、手办等周边的视觉素材。每轮在 HTML 工作台看图、比较和确认，最后交付顺序图片、小红书文案和有序话题。 |

## 安装

告诉支持 Skill 的 Agent：

```text
请帮我安装这个 Skill：https://github.com/Jarden-Jin/jarden-skills/tree/main/skills/jarden-ip-visual
```

也可以使用命令安装：

```sh
npx skills add Jarden-Jin/jarden-skills --skill jarden-ip-visual
```

仓库为私有时，安装者需要相应的 GitHub 读取权限。命令安装需要 Node.js；它不是 Skill 核心流程的运行依赖。

## 使用与依赖

安装后可以直接说：

```text
用 jarden-ip-visual 把这张原创草图做成毛绒挂件，要一套小红书素材。
```

需要 Python 3.10+，图片处理需要 Pillow，完整流程还需要生图能力，以及能够独立看图且不继承创作对话的隔离 Agent。使用宿主已有生图能力时无需额外配置 Key。macOS 已验证，Windows/Linux 尚未实机验证。

默认交付干净无提示字的图片。场景图和实际发布图必须通过独立物理合理性审查；这属于视觉检查，不是数值仿真或工程证明。

页面确认经本机受控服务写回项目并返回回执；静态页面只保存草稿，不冒充已经采用。更新有独立计划、校验与备份，不会自动迁移用户项目。

配置、数据接收方、费用边界、测试和恢复方法见 [jarden-ip-visual 使用说明](skills/jarden-ip-visual/README.md)。项目和成果默认留在本地；调用图片服务时会发送提示词与指定参考图。不会自动发布社交内容。

## 许可证

当前未附带开源许可证。
