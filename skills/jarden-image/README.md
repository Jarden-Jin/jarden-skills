# Jarden Image Skill

通过 Jarden 的远程 HTTPS API 生成图片、查询模型能力、估算任务耗时、恢复不确定提交并下载结果。

## 安装

让支持 Skill 的 Agent 安装：

```text
请帮我安装这个 Skill：https://github.com/Jarden-Jin/jarden-skills/tree/main/skills/jarden-image
```

命令安装：

```sh
npx skills add Jarden-Jin/jarden-skills --skill jarden-image
```

## 首次使用

运行时需要你自己提供个人 API Key；仓库不包含任何 Key：

```sh
python scripts/client.py --personal-key-file /private/path/jarden.key models
```

客户端验证个人 Key 后，会在 Skill 目录之外的本机用户配置目录保存个人及设备 Key。这是受本机文件权限保护的明文文件，不是系统钥匙串；有本机账户读取权限的人仍可访问。首次运行时也可以在交互终端隐藏输入。两个 Skill 共享同一服务的凭据缓存。

也可以直接使用已有设备 Key，绕过个人 Key 保存和设备注册：

```sh
python scripts/client.py --token-file /private/path/device.key models
```

不要把 Key 放进命令历史、提示词、日志、截图或 Git 提交。

## 生成图片

```sh
python scripts/client.py image \
  --model moss \
  --prompt "A blue ceramic cup in soft daylight" \
  --width 1024 --height 1024 --steps 25 \
  --key image-cup-001 --wait --out ./outputs
```

也支持目录声明可用时的专用 H3 Image：`--model h3`。先运行 `models`，以当前服务返回的就绪状态和参数限制为准。

## 数据与边界

- Skill 不包含模型权重，也不部署服务端。
- 提示词、指定的参考图片和任务参数会发送到配置的 Jarden API 地址，默认是 `https://ai.comark.work`。
- 结果从该 API 下载到本机；生成任务由服务端 GPU 执行。
- 图片 Skill 不支持视频采样后端；视频请使用 `jarden-video`。
- 需要 Python 3.12+ 和网络；macOS 已实际生成验证，Windows/Linux 客户端路径已实现但尚未在本次发布中实机验证。Windows 使用 `py -3.13`，macOS/Linux 使用可用的 `python3`。
- `npx` 安装方式需要 Node.js，但 Skill 客户端不需要 Node.js。
- 费用、速率限制、模型许可和服务可用性以服务端当前返回为准。

## 恢复与校验

客户端会保存原始请求和任务 JSON。提交响应丢失时，使用同一个请求文件和幂等编号恢复，不要换 Key 或重新猜测任务：

```sh
python scripts/client.py resume-request \
  --file ./outputs/request-ACTUAL.json --wait --out ./outputs
```

下载结果会校验服务端返回的 SHA-256。

仓库校验可在仓库根目录运行 `python3 tools/check_public_api_skills.py` 和 `python3 -m unittest discover -s tests -p 'test_public_api_skills.py' -v`；这些检查不会调用线上生成服务或消耗 GPU 额度。
