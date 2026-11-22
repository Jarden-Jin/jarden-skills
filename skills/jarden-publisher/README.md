# Jarden-publisher

把已定稿的图文准备到小红书、抖音、B 站专栏、TikTok Photo Post 或 Instagram 多图轮播。传图前集中检查账号和发布设置，准备后回读封面、图序、文案和话题。未指定平台时只准备小红书，默认停在发布前。

## 安装

让支持 Skill 的 Agent 安装：

```text
请帮我安装这个 Skill：https://github.com/Jarden-Jin/jarden-skills/tree/main/skills/jarden-publisher
```

或使用命令：

```sh
npx skills add Jarden-Jin/jarden-skills --skill jarden-publisher
```

## 配置与依赖

本地校验需要 Python 3.10+ 和 Pillow，可用 `python3 -m pip install Pillow` 安装。在线环节用受控浏览器，在各平台官方创作页登录。批量浏览器预检和小红书上传脚本使用 Ego Lite，其余操作按实际创作页执行。无需独立 API Key。权限提示和验证码由用户在浏览器中处理。命令安装器单独需要 Node.js。

## 使用

> 用 Jarden-publisher 把这个 post/ 带到小红书，停在发布前。

> 用 Jarden-publisher 把这组图文准备成 TikTok Photo Post 和 Instagram 多图轮播，先给我看图序、文案和发布设置。

也可以提供自己选好的图片、现成标题和正文。要做 B 站专栏时，指定平台、提供文章及图片，Skill 根据段落安排插图。指定多个平台时，各自保留状态。

传图前一起预检登录、验证和所需权限，并列出封面、文案和关键选项。TikTok 使用照片帖子标题与描述，Instagram 使用一份共用说明，轮播首图作封面。网页没有图文入口时，会给出按顺序选图、粘贴文案和核对设置的手机交接清单。

## 交付、数据与边界

任务目录保存校验过的 `package.json`、`job.json` 和实际回读证据。可在创作页确认最接近发布效果的实际版式。恢复时从原任务找原账号和稿件，只有平台有回执时报告提交或公开结果。

预检与计划留在本机。上传时只有选定的图片、文案和发布字段发送给相应创作平台。任务记录包含账号标识与浏览器位置，不保存密码、Cookie 或验证码。Skill 不负责生图、写长文或视频投稿。

本地脚本支持 macOS 和 Linux。小红书、抖音及 B 站已在 macOS 的 Ego Lite 实测草稿准备和回开。最终提交须有本次明确授权、平台许可及稿件核对证据。定时或同步发布由用户从创作页操作。

海外 TikTok、Instagram 的本地内容包、预检和任务状态已纳入回归测试。在线操作以账号实际提供的官方图文编辑器为准。两个海外渠道的登录后编辑和草稿回开待实测。

## 测试与排查

在本目录运行 `python3 -B -m unittest discover -s tests -v`。测试使用临时图片与模拟页面证据，不连接创作账号。若编辑器、账号或权限改变，按 [预检与审核](references/preflight.md) 回查原任务，不重复上传。
