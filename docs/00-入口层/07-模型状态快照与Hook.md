---
name: 模型状态快照与Hook
description: 每版本状态 JSON、会话级最小加载及无 hook 后备入口
tags: [kind/guide, topic/mlops, entry]
---

# 模型状态快照与 Hook

每个模型版本目录的 `workflow-state.json` 是生成索引，供人和智能体快速判断
“这个版本当前处于哪一阶段、测点资料是否适用、仓库里有什么发布记录”。
事实仍以同目录 `model.yaml`、`decisions.yaml`、`input-bindings.yaml` 和
`validation/publications/` 的逐 Run 回执为准。不要手改 JSON，也不要把它
当作公司平台实时状态。

```powershell
model-lab context models/_drafts/<目录名> --task build
model-lab context models/_drafts/<目录名> --write
model-lab context --all --check
```

第一条只读返回该模型版本的当前事实和本任务最少需看的文档；第二条重新生成
同版本 JSON；第三条供同事或 CI 查全库漂移。正常新建、维护分叉、阶段推进和
成功发布由 CLI 自动刷新，无需在每个步骤前询问。

JSON 的 `model.lifecycle_state` 是本地门禁，不是上传状态。
`publication.status=verified_repository_receipt` 只证明保存过已完成 Run、
远端下载摘要和平台路径预测均通过的机器回执；`live_platform_status` 仍为
`not_queried`。旧版 `model.yaml` 中的 Run 只标
`recorded_legacy_unverified`；仅有 Markdown 回执、缺少机器回执时标
`unknown`。这不会否定人工记录，但防止把历史叙述当作本次实时核验。
已有 Run 若只有文字回执，可在 Linux 3.11 上执行
`model-lab verify-publication <model.yaml> --experiment <实验名> --run-id <Run ID> --company-package <公司ZIP>`：
它只读查询远端实验和 Run、下载核对固定实包并重载预测，全部通过后才在本地
补逐 Run 机器回执和状态 JSON；预测会执行该已验收模型并读取 IoT，失败不会
创建 Run 或上传制品。

`serving.point_metadata=not_applicable` 是数值入参模型的正常状态。测点数组
模型使用 `input-bindings.yaml` 保存设施名称、设备名称、数采点位编号、信号
编码、信号名称、应有信号和完整取数编码；状态 JSON 只放绑定文件路径、摘要
与完整度，不复制一套可能漂移的点表。老版绑定缺独立点位编号或信号编码时
会标 `legacy_code_only`，不是伪装成 2.1 已补齐。

仓库已交付只读 Codex hook；它在会话开始、压缩恢复和新提问时尝试定位明确的
模型 ID＋语义版本，提供最小文档指针。首次出现多个候选版本时应只问一次，
不能猜；会话焦点保存在本机临时目录，不进入 Git。Codex 需要先信任仓库 hook；
如果没有加载 hook，直接从 `AGENTS.md` 和本页进入，执行 `model-lab context`
与 `model-lab status`，流程仍完整。hook 既不上传，也不替用户回答业务决定。

依据：[ADR 0018](../adr/0018-版本状态快照与会话级Hook.md)。
