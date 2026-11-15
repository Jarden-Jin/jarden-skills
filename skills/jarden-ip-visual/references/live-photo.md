# IP 成图 → Live Photo

本能力是已确认成图的可选下游，不改四组 21 项编号、不要求静态资产补做视频。默认不启用、不调用外部服务、不写照片图库。Live Photo 是配对的静态照片与 MOV，不是改扩展名、GIF、普通 MP4 或网页缩放。

## 目录

- [边界与准备](#边界与准备)
- [计划与授权](#计划与授权)
- [官方图生视频](#官方图生视频)
- [真实视频登记](#真实视频登记)
- [本机封装](#本机封装)
- [分目标验收](#分目标验收)
- [失败回执与恢复](#失败回执与恢复)
- [恢复与交付](#恢复与交付)

## 边界与准备

静态成图仍默认宿主原生生图。只有用户明确选择 Live 图生视频服务时才使用官方 LibTV CLI，不自动换模型、转接 API、充值或开会员。仅本轮明确启用时读取其命令手册；没有该 Skill 时以 `libtv <命令> --help` 为准。凭据由官方 `libtv login web` 管理，使用 `libtv account info` 验证实际登录；不读取令牌文件或把回调 URL 保存到项目、日志或 Git。

项目状态入口仅需 Python 3.10+ 标准库。原生封装与格式校验需要 macOS、FFmpeg/ffprobe 和 PyObjC 的 Cocoa、Quartz、AVFoundation、CoreMedia、Photos 绑定。缺依赖先说明，由用户决定是否安装在单独虚拟环境；不能为静态用户自动装这些依赖。无原生环境时可以记录视频和待封装状态，不能交付伪 Live 文件。Windows/Linux 原生封装未支持。

选择已经确认的场景图或单品图，核对人物/角色身份、五官、色彩、尺寸、真实像素和来源。小图不能靠补边或放大冒充高清；必要修图仍使用已授权图片渠道。计划中的来源文件必须位于项目内，源字节改变后重新核对，不沿用旧授权、视频或验收结论。

## 计划与授权

```text
<python> <skill>/scripts/project.py live <项目> plan --source post/04-scene.png --model star-video2.5-draft --prompt "固定机位，保持五官、轮廓、缝线和配色，仅绒毛自然轻摆；禁止眨眼、呼吸、走动、变形和新增肢体" --evidence "用户确认采用这张成图并要求制作 Live 样片"
<python> <skill>/scripts/project.py live <项目> status
```

保存独立任务及来源摘要；不把计划当成已经执行。采用低成本短样片先检查动作，用户选定后且账户有资格再精修或转正片。时长、分辨率、费用与水印以当次模型 schema 和账户为准，不承诺固定耗时、免费或无水印。

外部执行前先登记 `live <项目> begin --job <任务编号> --evidence "用户对本次服务、样片范围及费用边界的明确授权"`，仅 `should_call=true` 才发起新生成。已有 requesting/unknown 先核对官方节点和回执，不重新提交计费任务。该状态门槛不能阻止 Agent 在其他工具里越权调用，Agent 必须遵守。

`begin` 的 `request.live_duration` 是期望的实况片段长度，不是视频服务的生成时长。视频时长必须另按当次模型 schema 选取，例如模型最短四秒时先生成四秒，再本机取三秒，不能把三秒直接发给不支持的模型。

## 官方图生视频

在独立项目子目录绑定画布，不改其他创作的默认画布。所有 LibTV 操作只通过官方 CLI；以下是结构示例，模型名与枚举需从当次 schema 读回。

```sh
libtv account info
libtv model search -t video
libtv model star-video2.5-draft
libtv project create "IP Live 验证" -w 0
libtv project use <画布UUID>
libtv upload "Live首帧" -p <画布UUID> -t image -f <任务输入副本>
libtv upload "Live尾帧" -p <画布UUID> -t image -f <同一任务输入副本>
libtv node create "Live样片" -p <画布UUID> -t video -s "model=<当次modelName>" -s modeType=frames2video -s ratio=3:4 -s resolution=480p -s duration=4 -s count=1 -s enableSound=off --left <首帧节点ID> --left <尾帧节点ID> --prompt "<已保存的完整微动态提示词>"
libtv node <视频节点ID> -p <画布UUID>
libtv node <视频节点ID> -p <画布UUID> --run
libtv download -n <视频节点ID> -p <画布UUID> --watermark true -o <项目内下载目录>
```

首尾同帧用两个独立资源节点，读回实际 `imageList` 顺序与数量为 2 后才执行。`--left` 对同一节点可能去重，重复两次同 ID 不是两个参考。首尾约束不能保证循环无缝，裁剪后必须重新看片。模型不支持首尾帧时保留待选择，未经授权不改模型。`node --run` 自己等待终态，不额外后台运行或按超时盲目重发。

创建画布可能返回 `projectMeta.uuid`，创建节点可能返回 `newNodeKey`；解析失败先从真实回执恢复同一对象。`status=2` 与实际资源下载须核实，退出码或节点存在不等于有可用视频。会员拒绝、额度不足记为明确阻塞；网络或响应不明记为待对账，不购买或自动重试。

`--watermark` 必须匹配账户资格：非会员保留 `true`，会员按官方资格使用 `false`。禁止裁去、擦除或绕过社区水印。样片转正片另用官方 `video final`，失败保留样片及拒绝原因，不将低清放大称高清。

## 真实视频登记

保存干净的官方任务回执到项目文件，不含 token、签名下载 URL 或完整账号信息。登记 JSON 至少包含任务编号、实际视频文件、回执的路径和 SHA-256、动作检查、样片/正片级别以及水印状态，字段以 `live record --help` 为准。

抽看首、中、尾和关键动作，正常速度播放核对眼鼻间距、配色、缝线、四肢、接触、毛发和背景。玩具不能无授权变成真实动物；失败或不确定先返修，不进入封装。单纯摄像机缩放如采用须标「二维运动」，不能冒充真实主体微动态。

```text
<python> <skill>/scripts/project.py live <项目> record --file <视频登记.json>
```

动作评审是宿主或人的观察声明，程序仅验证文件、摘要及字段，不声称自动证明画质。复用已有真实视频时可从计划直接登记可信回执，不为补流程重新付费生成。

## 本机封装

```text
<python> <skill>/scripts/project.py live <项目> package --job <任务编号>
```

使用实际视频帧制作封面，保留原分辨率和水印；默认生成约三秒测试片，保留截取范围与关键帧时间。照片 MakerApple `17` 与 MOV 的 `com.apple.quicktime.content.identifier` 必须相同；MOV 包含与视频关联的 `still-image-time` 定时元数据。读回标识、尺寸、时间轨和非降级 `PHLivePhoto` 解码结果，不接受只有 UUID 的弱检查。

输出到唯一目录，已有目标不覆盖；失败保留中间文件和原因。交付 JPG/MOV、`.pvt` 导入包和校验 JSON，ZIP 仅为运输容器，不是实况格式。封面来自动态帧，不能称原图字节未改；来源母图另行保留。

## 分目标验收

| 层级 | 通过证据 | 不能替代的层级 |
| --- | --- | --- |
| 本机格式 | 当前文件配对、时间轨、尺寸与完整解码 | 不代表已导入图库 |
| Mac Photos | 显式授权导入为一个实况资产，原生播放与动态帧可见；建议导出原片复核双资源 | 不代表 iPhone 或发布应用通过 |
| iPhone Photos | 具体手机与系统、传输路径、实况徽标和长按播放均确认 | 不代表小红书等识别 |
| 发布应用 | 指定应用、版本和实际选择后预览识别；不实际发布 | 不代表所有应用或锁屏壁纸 |

导入前先取得向照片图库写入的明确授权，说明 iCloud 开启时可能同步。本 Skill 不默认调用图库写入。Mac 可用 Photos「文件 → 导入」选择 `.pvt`；识别徽标后仍须使用原生「开始播放」或实况时间轴查看不同动态帧。出现错误先处理，不能看到 LIVE 字样就通过。

导出未修改原片应得到配对静态图与 MOV，读回 UUID 和摘要核对未丢失。向 iPhone 传递时优先从 Photos 分享并保留原始数据；裸文件、压缩传输、聊天软件转存可能丢配对，最终按目标设备实际结果验收。无法操作手机时保留待验收，不虚构成功。

每个验收目标用 `live <项目> attest --file <验收.json>` 分别登记。证据应含目标、观察者、徽标、播放、实际文件摘要和本地证据文件；截图/手工记录只能作为观察依据，不宣称密码学证明人已看过。更换文件或来源后旧验收失效。

Apple 官方参考：[显示与播放 Live Photos](https://developer.apple.com/documentation/photokit/displaying-live-photos)、[PhotoKit 资产及请求](https://developer.apple.com/documentation/photokit/fetching-objects-and-requesting-changes)。

## 失败回执与恢复

官方明确拒绝但未产生视频，或请求结果不明时，保存脱敏回执，执行 `live <项目> reconcile --file <对账.json>`。文件包含 `job_id`、`status`（`failed` 或 `unknown`）、`provider_receipt` 的项目相对路径与摘要、`evidence`；建议同时带 `begin` 返回的 `claim_id`、当前计划 `signature` 和来源 descriptor，错误绑定会被拒绝。

对账只记录已有请求结果，不重置计划或返回新调用许可；重复登记同一回执幂等。原请求后来成功时，用 `record` 登记真实视频。明确失败需要新制作时先重新确认方案、授权与账户资格，不自行改参数或重复计费。封装失败则显式重跑 `package`，使用原视频和唯一新目录，不重新生成视频。

## 恢复与交付

项目 `live_photo` 只在用户选用时出现，`live status` 读取当前真实状态。工作台独立展示原图、视频预览、配对下载和各目标验收；浏览器视频播放不是原生实况播放验收。静态主线的 next 和原 21 项能力不被 Live 失败阻塞。

最终明确：样片或正片、真实分辨率、水印、源文件保持、配对是否通过、哪个图库/设备已播放、哪些目标未验收。不能承诺无缝循环、商用质量、锁屏壁纸或自动发布。已授权范围内续作，不把失败后换供应商/充值当默认恢复。
