# 0049 _drafts 出口判据：verified 回执加 archive-release --apply

> 本 ADR 保留私有源仓原文；`step-check`、`ledger`、`.dsh-hooks` 在本公开框架分发仓未实现，请以 `status`、`context`、`.codex/hooks.json` 为准。

- 状态：已接受
- 日期：2026-10-20

## 背景

`models/_drafts/` 下当时有 23 个工作区（每个都有 `model.yaml`；2026-10-20 归档四个、退役十三个之后剩十个），其中若干已到 `releasable`。
"什么时候可以把一个草稿工作区搬进 `models/0X_<平台名>/`"此前散在规则表、流程文档与命令帮助三处，
判断依据各不相同；更实际的漏洞是**手搬目录**——`git mv` 一个工作区进正式目录，绕过全部判据，
而且不会生成 `releases/<version>.yaml` 快照、不会登记 catalog，事后没人能从资产里看出这一代是怎么来的。

机器侧其实只有一条提升通道：`model_lab/archival.py` 的 `archive_workspace()`，
由 `model-lab archive-release` 调用。它的前置条件是确定的、可以逐条核对的；
缺一份 `verified` 发布回执时它直接用错误文本拒绝
（"no validation/publications receipt; publish this version before archiving"），
回执与版本或制品摘要不匹配时同样拒绝
（"no verified publication receipt matches this model version and bundle"）。
既然判据已经实现，缺的只是一份写下来的出口口径。

## 决策

1. **唯一出口是 `archive-release --apply`**：`& .\scripts\model-lab.ps1 archive-release <工作区> --target-name <NN_名称>`。
   不带 `--apply` 只输出计划（dry-run），不落盘；不得用 `git mv` / 复制目录等方式手工提升。
2. **五条硬前置缺一不可**（对应 `model_lab/archival.py` 的实现，按其检查顺序）：
   1. 工作区在 `models/_drafts/` 下，且带 `model.yaml`（草稿以外的东西不允许提升）；
   2. `--target-name` 形如 `NN_名称`（两位数字、下划线、无路径分隔符）；
   3. 当前生命周期状态为 `releasable` 且全部门禁通过；
   4. `release-bundle/service` 的目录摘要等于 `model.yaml` 记录的 MLflow 服务制品摘要；
   5. `validation/publications/` 下存在一份 `status: verified` 的发布回执，且其模型 id、版本、
       `bundle_sha256` 与本代完全匹配。"是否已验证发布"的判定仍以 ADR 0031 的唯一权威
       （`workflow_state._publication_state`）为准，归档入口的校验是它的子集复核。
3. **目标目录已存在时必须显式升级**：传 `--upgrade-existing` 表示把维护版发布进它分叉自的在役资产；
   旧代次的快照与回执进 `release.history`，不覆盖、不改写历史。不加该开关则直接拒绝
   （"`<target>` already exists"）。
4. **不得手搬、不得事后补录**：手搬的工作区没有 `releases/<version>.yaml` 快照、没有回执联动，
   catalog 也读不到它；发现手搬按越界产物处理并回退到 `_drafts`，走正式通道重来。
5. **退役走归档记录，不删历史**：不再维护的旧代次在 `docs/90-归档/` 留一份「<模型> 归档记录」
   （现有 `44-前馈气量-改-归档记录.md`、`45-前馈气量-改-0.2.0-归档记录.md` 即此形式），
   历史审计证据由归档分支保存（ADR 0020），工作树只留当前一代；不删除资产目录、不重写已发布快照。
6. **多代次各自留快照，目录与资产名不按版本改**：同一资产内的每一代都保留自己的
   `releases/<version>.yaml`（`01` 有 1.0.0/2.0.0，`09_前馈气量-改` 有 0.1.0/0.2.0），
   不合并成一份；不同模型/不同平台名的资产是各自独立的目录（`02_前馈气量模型` 与
   `09_前馈气量-改` 是两个资产），不因命名相近就并进同一个 target。
7. **收口按判据体检，不按目录名猜**：处理一大批工作区时逐个跑 `status` / `step-check` 取门禁状态，
   再落三选一——`archive-release`（已具备回执）、补证据（缺回执/缺门禁）、写归档记录（放弃维护）；
   禁止凭"目录看起来像正式模型"就移动。

## 后果

- 提升动作可复现：一条命令、一份回执路径、一份版本快照；归档后按命令提示重跑
  `catalog --check` 让中央清单与本仓一致。
- 规则表里"verified 回执后经 `archive-release --apply` 提升、不得手工移动"这条既有表述
  （`docs/20-规则层/20-硬性规则速查.md` 第 51 行）从此有实现与判据支撑，不再是口头约定。
- **已知限制**：`archive-release` 只覆盖 `model_lab` 自己的打包发布通道；读数式模型走集团技能包
  A 通道，其证据是部署回执与验收记录，不经过本命令（通道判别见
  `docs/10-流程层/27-模型发布路径选择与集团部署技能包.md`）。
- 代价：草稿到在役比"直接 mv"多几步（回执、摘要、门禁）；换来的是每一代在役资产都能追溯到实验与 Run。

## 被否决的方案

- **`step-check` 全绿就允许提升**：`step-check` 只核每步交付物，不核发布回执与制品摘要，
  会把"文件齐了"当成"已发布"。
- **保留"手工 mv 后补录快照"**：补录依赖人的记忆，`releases/` 与 catalog 必然缺项，
  事后无法区分"补录"与"伪造"。
- **按目录名或版本号猜是否已发布**：正是本 ADR 要消掉的问题——判据只能来自回执与门禁。
- **多代次合并成一个目录或一个 target**：会覆盖历史发布记录、破坏 `release.history`，
  与 ADR 0007 的"在役资产持续留档"冲突。
