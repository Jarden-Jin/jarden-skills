# jarden-ip-visual

把原创 IP 的线框图、灵感图或平面 AI 图，逐步做成毛绒、手办、拼图等实体周边的商业视觉。它会先问清楚要做什么，再分阶段给方向、用 HTML 对比，最后交付可以直接发小红书的顺序图片、标题、正文与 tag，减少反复摸索和漏项。

## 会得到什么

- 一次只问一个问题，已回答的内容保存在项目里，继续会话时不重复问。
- 四种笔记结构：同系列合集、IP 设计手稿、单个玩偶多维展示、多个玩偶多场景展示。
- 从概念、材质到透明资产、场景图的逐轮对比页，每轮默认三个方向。
- 编号并统一为3:4的发布图片、可复制的文案与有序话题；九宫格可另附预览。
- 默认干净无提示字的成图，AI 与未知规格说明留在文案和 HTML；只有你明确要求才加图字。
- 所有场景备选、编辑/合成图和实际发布图，逐张交给不继承制图对话的独立 Agent 检查重力、连接件、人体接触和光影；没通过不交付定稿。

图片能力负责生成，本 Skill 负责需求确认、参考一致性、阶段确认、验收和最终整理。它不能保证每次一遍出图，用户可以补图或重做。

## 安装

本 Skill 位于 [Joseph-Jin/jarden-skills](https://github.com/Joseph-Jin/jarden-skills) 的 `skills/jarden-ip-visual` 目录。仓库为私有时，安装者需要相应的 GitHub 读取权限。

最方便的方式是告诉 Agent：

```text
请帮我安装这个 Skill：https://github.com/Joseph-Jin/jarden-skills/tree/main/skills/jarden-ip-visual
```

也可使用支持多 Skill 仓库的安装器：

```text
npx skills add Joseph-Jin/jarden-skills --skill jarden-ip-visual
```

手动安装时复制当前 jarden-ip-visual 目录到宿主支持的 skills 目录。npx 的 Node.js 依赖只属于可选安装方式。

## 开始使用

可以直接说：

```text
用 jarden-ip-visual 把这张原创草图做成毛绒挂件，要一套小红书素材。
```

也可以说「继续上次项目」「第二个方向再柔软一点」「把这组做成九宫格」。不需要背内部命令。

开始前提供一张或多张图，或清楚的文字想法。告诉 Agent 已经确定的品类、角色数量和用途，就可以少回答相应的问题。

## 配置与依赖

使用宿主内置或已连接的生图能力时无需额外配置 Key。优先可用的高质量图像模型，实际模型取决于宿主。

| 能力 | 依赖 |
| --- | --- |
| 需求、状态与 HTML | Python 3.10+，宿主可读取图片与写本地文件 |
| 检查、去底、拼版、发布整理 | Pillow 和可用中文字体 |
| 真实生图 | 宿主内置生图、已连接服务或兼容图片接口之一 |
| 场景物理验收 | Pillow 检查盲审图元数据；宿主支持新建不继承对话、能实际看图的独立 Agent；没有时停止在待审 |
| 可选本机凭据输入页 | Node.js 22.18+、组件依赖及系统凭据服务 |

图片处理依赖可安装 `python3 -m pip install Pillow`；Windows 通常用 `py -3`。字体自动寻找系统中文字体，也可设置 JARDEN_IP_VISUAL_FONT。

需要第三方图片服务时，Agent 会先检查已有配置与脱敏状态，再提供本机 Key 输入页。Key 留在系统凭据库，不要发在聊天里。普通接口设置单独存用户配置目录，支持 JARDEN_IP_VISUAL_HOME。

凭据组件首次安装依赖需在 scripts/credential-ui 目录运行 `npm ci --ignore-scripts`。详细步骤见 [图片后端](references/image-backends.md) 与 [组件说明](scripts/credential-ui/README.md)。

## 兼容范围

macOS 是当前验证平台。Python 脚本使用 pathlib 和跨平台接口；Windows/Linux 的业务流程尚未实机验证。

凭据组件使用 macOS Keychain、Windows Credential Manager 或 Linux Secret Service。后两者有适配器，尚未在本 Skill 中实机验收；不可用时停止，不转为明文存储。

没有生图工具时可以保留提示词和带缺图标记的对比页，不能完成实际素材。没有 GUI 仍可生成 HTML，用户在自己的浏览器打开；离线时可整理已有图，不能调用云端生图。

物理审查是视觉可信度检查，不是数值仿真或工程证明。报告绑定图片哈希；旧项目缺审、改图或重新排版后需要补审。程序能校验报告声明和哈希，不能自动证明审查者真的独立，也不能保证从单张图判断全部物理事实。见 [物理审查](references/physics-review.md)。

## 数据与功能边界

项目记录、原图副本、生成图片和 HTML 默认留在用户选的本地目录。调用图片能力时，提示词和指定的参考图会发到该能力背后的服务；具体接收方与费用以用户选择的服务为准。

API Key 保存在当前用户的系统凭据库，运行时只注入可信业务程序；不自动同步。这减少凭据进入聊天的机会，不是对同用户任意代码执行的强隔离。

只制作设计与发布素材，不做3D建模、工程打样文件、商品上架、自动发布、版权确权或商业效果保证。普通单图、单纯抠图和非 IP 文案交给对应工具。

保留原创空间：四类结构规定每张图的作用，不固定角色造型或配色。未经提供的价格、尺寸与发售时间不会写成事实。AI 说明保留在文案、HTML及发布页声明，不默认印在图里；打包器不能自动识别并擦除原图已有文字。

## 测试与排查

在本目录运行：

```text
python3 -m unittest discover -s tests -v
```

凭据组件的检查在它自己的目录运行 `npm run check`、`npm run build`、`npm test`；原生假凭据验证用 `npm run test:native`，会清理随机测试项。

失败后保留项目，告诉 Agent 继续同一个目录。不要删除已经生成的图；接口超时先核对服务端记录，防止重复收费。

透明背景问题可运行 check_images.py --alpha；发布整理先用 package_post.py --dry-run 查看缺项。图片比例、标题和话题采用保守交付预算，发布前以当前账号发布页的校验为准。

## 源码与许可证

源码维护在 [Joseph-Jin/jarden-skills](https://github.com/Joseph-Jin/jarden-skills)，版本号以 SKILL.md 的 `metadata.version` 为准。当前未附带开源许可证。Windows/Linux、较弱模型及第三方线上图片接口尚未完成实机验证。
