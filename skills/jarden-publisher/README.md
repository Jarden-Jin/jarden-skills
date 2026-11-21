# Jarden-publisher

把已定稿的图片和文案准备到小红书图文、抖音图文或 B 站专栏。传图前集中检查账号和发布设置，保存后回读封面、图序、文案和话题。未指定平台时只准备小红书，默认停在发布前。

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

也可以提供自己选好的图片、现成标题和正文。要做 B 站专栏时，指定平台、提供文章及图片，Skill 根据段落安排插图。指定多个平台时，各自保留状态。

传图前会对所选平台一起预检登录与位置权限，并列出本篇的封面、文案和关键选项。交付时可以核对各站实际采用的选项与草稿恢复情况。

## 交付、数据与边界

任务目录保存校验过的 `package.json`、`job.json` 和实际回读证据。可在创作页确认最接近发布效果的实际版式。恢复时从原任务找原账号和稿件，只有平台有回执时报告提交或公开结果。

预检与计划留在本机。上传时只有选定的图片、文案和发布字段发送给相应创作平台。任务记录包含账号标识与浏览器位置，不保存密码、Cookie 或验证码。Skill 不负责生图、写长文或视频投稿。

本地脚本支持 macOS 和 Linux。已在 macOS 的 Ego Lite 实测三平台草稿准备和回开。最终提交未做线上实测；只在平台允许、稿件核对且用户明确授权时走提交分支。定时或同步发布由用户从创作页操作。

## 测试与排查

在本目录运行 `python3 -B -m unittest discover -s tests -v`。测试使用临时图片与模拟页面证据，不连接创作账号。若编辑器、账号或权限改变，按 [预检与审核](references/preflight.md) 回查原任务，不重复上传。
