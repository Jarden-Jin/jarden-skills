# Jarden Video Skill

通过 Jarden 的远程 HTTPS API 生成 MiniMax H3 视频、查询模型能力、估算耗时、提交首帧视频任务、恢复不确定提交并下载 MP4。

## 安装

让支持 Skill 的 Agent 安装：

```text
请帮我安装这个 Skill：https://github.com/Jarden-Jin/jarden-skills/tree/main/skills/jarden-video
```

命令安装：

```sh
npx skills add Jarden-Jin/jarden-skills --skill jarden-video
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

## 文生视频

先查看当前服务可用配置：

```sh
python scripts/client.py models
python scripts/client.py estimate \
  --kind video --width 1920 --height 1080 \
  --duration-seconds 10 --steps 4 \
  --sampling-profile turbo-4step --render-mode upscale
```

提交一个 Turbo 放大输出示例：

```sh
python scripts/client.py video \
  --prompt "A blue cup on a table. Slow camera movement, warm daylight, quiet ambience." \
  --width 1920 --height 1080 --duration-seconds 10 \
  --sampling-profile turbo-4step --attention-backend comfy-kitchen \
  --render-mode upscale --key video-cup-001 \
  --wait --out ./outputs
```

`upscale` 先以较低分辨率生成再放大，不等同于原生 1080p 细节。不要把单次估时当作完成时间保证。

## 首帧视频

首帧图片的宽高比应与输出一致，服务不会自动裁剪或加黑边：

```sh
python scripts/client.py upload --file /absolute/reference.png
python scripts/client.py video \
  --first-frame asset_ACTUAL_ID \
  --prompt "Slow camera movement, consistent subject, quiet natural ambience" \
  --key first-frame-001 --wait --out ./outputs
```

## 数据与边界

- Skill 不包含模型权重，也不部署服务端。
- 提示词、指定的首帧图片和任务参数会发送到配置的 Jarden API 地址，默认是 `https://ai.comark.work`。
- 结果从该 API 下载到本机；生成任务由服务端 GPU 执行。
- 需要 Python 3.12+ 和网络；macOS 已实际生成验证，Windows/Linux 客户端路径已实现但尚未在本次发布中实机验证。Windows 使用 `py -3.13`，macOS/Linux 使用可用的 `python3`。
- `npx` 安装方式需要 Node.js，但 Skill 客户端不需要 Node.js。
- 费用、速率限制、模型许可和服务可用性以服务端当前返回为准。

## 恢复与校验

客户端会保存原始请求和任务 JSON。提交响应丢失时，使用同一个请求文件和幂等编号恢复，不要换 Key 或重新猜测任务：

```sh
python scripts/client.py resume-request \
  --file ./outputs/request-ACTUAL.json --wait --out ./outputs
```

下载的 MP4 会校验服务端返回的 SHA-256；输出还应按实际任务检查尺寸、时长、音轨和是否为放大结果。

仓库校验可在仓库根目录运行 `python3 tools/check_public_api_skills.py` 和 `python3 -m unittest discover -s tests -p 'test_public_api_skills.py' -v`；这些检查不会调用线上生成服务或消耗 GPU 额度。
