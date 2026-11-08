# 项目状态与命令

所有项目状态都在用户选择的项目目录，Skill 自身不保存项目、用户偏好或图片。JSON 使用 UTF-8，命令返回 JSON。

## 项目结构

```text
<项目>/
  project.json
  inputs/
  rounds/<轮次>/round.json
  rounds/<轮次>/A.png
  reviews/physics/<request_id>/packet/
  reviews/physics/index.json
  copy.json
  gallery.html
  post/01-cover.png
  post/caption.md
  post/manifest.json
```

输入类型：wireframe、sketch、flat-design、ai-image、photo、text、reference。主流程命令的 `<项目>` 是目录参数，路径含空格时作为单个参数传入。

## 建立、查看和保存答案

```text
<python> <skill>/scripts/project.py init <项目> --input <图> --kind wireframe
<python> <skill>/scripts/project.py show <项目>
<python> <skill>/scripts/project.py set <项目> answers.product_form=plush answers.subject_scope=single
<python> <skill>/scripts/project.py set <项目> 'answers.subjects=["角色名"]' answers.note_type=single-multiview
<python> <skill>/scripts/project.py set <项目> answers.concept_count=1 answers.delivery=full-post image_backend.kind=builtin
```

JSON 列表的引号按当前 Shell 处理，或通过参数数组调用。相同 init 重复运行只读已有项目，不清空。

answers 存确认过的回答；brief 存角色名、卖点、产品补充与 bible；preferences.directions_per_round 存每轮方向数，默认3；image_backend 存能力和普通设置，不存凭据。

修改品类（含其他周边的具体名称）、角色或概念数量会失效后续定稿；改笔记类型与交付形式会失效场景与发帖，较早阶段尚未完成时仍留在较早阶段。旧文件保留，不再展示成当前发帖。

角色名必须唯一，同名角色加区别词。缩减名单时同步数量；只减少数量但未说明保留哪只会被拒绝。已明确要增加独立设计时，可设更大的 concept_count，新增设计仍分别确认。

## 使用已经确认的输入

只有用户明确认可现有概念或材质时，才登记：

```text
<python> <skill>/scripts/project.py accept-input <项目> --stage concept --subject <角色> --path inputs/design.png
```

登记一张概念不等于批准另一只角色，也不等于材质已选定。路径在项目内，不调用生图。

## 开轮和登记

```text
<python> <skill>/scripts/project.py new-round <项目> --stage concept --subject <角色> --count 3
<python> <skill>/scripts/project.py record-round <项目> <轮次>
<python> <skill>/scripts/build_gallery.py <项目>
```

round.json 的每项都有 id、title、idea、prompt、image。生成结果填到 image 指定路径，method 默认 generate。

refs.source 是身份或产品参考，refs.style 是系列或已选场景风格，refs.based_on 是沿用某方向；路径相对于项目根目录。整组里 item.refs 可以指定单角色。

method=generate 的 prompt 至少40字；compose 与 derive 要写 sources 列表，说明来源。这只描述操作来源，不豁免视觉验收。

scene 轮在 record-round 前必须用 `physics_review.py prepare/record` 完成逐张零上下文物理审查；所有方法都适用。命令与报告格式见 [物理审查](physics-review.md)。缺审、未通过、不确定或换图后旧哈希不能继续；手动 final 也不能绕过。

## 选定与重做

```text
<python> <skill>/scripts/project.py select <项目> <轮次> A
<python> <skill>/scripts/project.py select <项目> <轮次> --approve --skip scene-2
<python> <skill>/scripts/project.py new-round <项目> --stage scene --kind set --slots scene-2
<python> <skill>/scripts/project.py new-round <项目> --stage material --based-on <轮次>:B
```

alternatives 是同一决策的备选，set 是一组有不同用途的图。概念与材质用 alternatives，透明资产用 set。只能选当前需求下已登记且实际存在的图片；superseded 不能重新登记激活，cutout 还要通过透明检查。

重做上游会撤销相关下游选择，不能复用旧材质和旧成图自动跳关。局部整组按必需图位累计核对缺口，不能用一张封面代替整套。系列九宫格无法容纳全部角色及必要场景时，先让用户决定分组、减少角色或改交付形式，不截断图位。

show.next 给出当前动作：ask、backend、round、generate、await-choice、package 或 done。优先遵循它，避免反复提问和跳阶段。

## 图像处理与整理

```text
<python> <skill>/scripts/check_images.py <图> --alpha
<python> <skill>/scripts/remove_bg.py <纯底原图> <透明PNG>
<python> <skill>/scripts/compose.py --help
<python> <skill>/scripts/package_post.py <项目> --dry-run
<python> <skill>/scripts/package_post.py <项目> --prepare-review
<python> <skill>/scripts/package_post.py <项目>
```

默认 `copy.image_text=none` 且 overlays 为空；只有用户明确要图字时才用 `user-approved`。`--prepare-review` 只输出实际排版的待发布图，不提交post或项目状态；这组新像素经独立物理审查后才正式打包。dry-run 只校验源图和文案，不代表最终输出通过。

发帖整理相同输入且输出仍通过物理审查时返回 unchanged，并修复中断时未提交的项目状态；交付形式参与签名。内容不同或损坏需 --force，旧版保留为 post-previous-*。九宫格预览在 post/previews 子目录，正式发布图片仍在 post 根目录。当前预览绑定有效项目的 manifest，不从磁盘旧目录猜定稿。旧项目缺审时保留文件，补独立审查后才能新批准或打包，不自动继承作者过去的视觉结论。

## 恢复与失败

先 show，再检查当前轮的缺图与接口回执。生图失败不重新 init，不删除已通过的方向；修复缺图后重新 record-round 和 build_gallery。

gallery.html 只是本地查看页，不保存用户选择。用户在对话回复后由 Agent 调用 select。
