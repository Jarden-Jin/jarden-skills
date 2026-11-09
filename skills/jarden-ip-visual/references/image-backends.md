# 图片能力、接口与本机配置

按能力选择，不让用户为了已有能力重复配置。普通设置和 API Key 分开保存。

## 选择顺序

1. 默认宿主原生或系统生图：发现工具、读取工具说明，无需额外 Key。
2. 只有用户明确选择外部服务时，检查已连接服务的模型和参考图能力。
3. 只有用户明确选择 API 路径时，核对兼容 Images 接口、本机配置与安全凭据。

先核实实际可用能力，不因模型名字猜测支持范围。默认能力不可用时保存待生成状态；不得因质量、路径或重试自动切换服务商、订阅转接或 API。生成前按 [产品与镜头](product-and-shots.md) 登记原生调用任务，未决请求先恢复，不重新发送。

把选择记到 project.json 的 image_backend，例如 kind=builtin、connected 或 api，附 model 与非敏感服务标识。真实工具返回的图保存到轮次目录；不得只写一份提示词就称已生成。

## API 后端的普通配置

image_api.py 支持同步的 `/images/generations` 和带参考图的 `/images/edits`，返回 data[].b64_json 或 data[].url。

它不适配所有供应商；异步任务、自定义鉴权或专用参数需要对应服务的专用适配器。失败保留回执，不自动重发可能收费的请求。

```text
<python> <skill>/scripts/image_api.py configure --base-url https://images.example.com/v1 --model <模型> --dry-run
<python> <skill>/scripts/image_api.py configure --base-url https://images.example.com/v1 --model <模型>
<python> <skill>/scripts/image_api.py status
```

地址不含 Key、用户名或密码。普通设置存用户平台配置目录，可用 JARDEN_IP_VISUAL_HOME 覆盖；不是项目目录或 Skill 仓库。

## 查询与首次填写 Key

已有安全凭据入口时先复用。使用随附入口需要 Node.js 22.18+，依赖已安装，以及当前系统用户可访问的凭据服务。

```text
node <skill>/scripts/credential-ui/src/profile.ts status image-api
node <skill>/scripts/credential-ui/src/profile.ts setup image-api
```

status：退出码0表示已配置，2表示缺失，1表示后端错误。已有凭据直接使用；只在缺失或用户要求更换时启动 setup。

将 setup 返回的本机链接展示给用户，让用户亲自填写保存。禁止自动操作真实 Key 页面，不要求把 Key 粘贴到聊天。页面里已配置项留空保留；partial 或超时先查状态，只补缺失项。

组件行为与恢复见 [凭据组件说明](../scripts/credential-ui/README.md)。只处理引用和脱敏状态，Key 不进入项目 JSON、命令参数、日志或对话。

## 实际业务调用

每张先写提示词文件和计划；参考图可以重复传 --reference。默认1024×1536，接口不支持时按文档调整；最终发布整理成1080×1440。

```text
<python> <skill>/scripts/image_api.py plan --prompt-file <提示词> --reference <参考PNG> --out <轮次/A.png> --plan <轮次/A-plan.json>
<python> <skill>/scripts/image_api.py run --plan <轮次/A-plan.json> --dry-run
node <skill>/scripts/credential-ui/src/profile.ts run image-api -- <python> <skill>/scripts/image_api.py run --plan <轮次/A-plan.json>
```

plan 和 dry-run 不联网、不取系统 Key。正式 run 发送提示词和指定参考图，可能按服务商价格收费；已有用户授权范围内继续执行，新增服务或费用边界不明时先说明。

运行时由包装器注入 JARDEN_IP_VISUAL_IMAGE_API_KEY，只读取这一项业务需要的 Key。受信的无界面运行环境也可预先注入该变量。

请求前跨进程锁定请求和输出，写入 requesting 回执。同一计划或输出并发运行时，后来的执行者不会再发生成请求。收到成功结果后立即保存本地 response 文件，并逐张记录进度；它可能含临时图片下载地址，只留在用户项目里，不进入发布包或日志。

下载或保存失败后运行 `<python> <skill>/scripts/image_api.py resume --plan <计划>`，只补解码、下载和保存，不需要 Key，也不重新 POST；已成功图片按哈希复用。没有成功响应的超时仍需到服务端核对，不自动重新生成。原始参考图在仅恢复下载时不必重新上传。

## 存储与兼容

macOS 使用 Keychain，Windows 使用 Credential Manager，Linux 使用 Secret Service/secret-tool 与用户 D-Bus 会话。不可用时停止，不自动安装、解锁或降级成明文。

组件属于当前系统用户，不自动同步或迁移。正常流程避免 Key 进入对话，不等于对同用户任意代码执行的强隔离。

组件的 macOS 路径有原生假凭据验证；Windows/Linux 适配器已实现，尚未实机验证。图片接口的业务绑定用本机假接口测试，不能据此宣称所有第三方服务兼容。
