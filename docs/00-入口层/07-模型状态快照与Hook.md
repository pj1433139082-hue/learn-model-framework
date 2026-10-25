---
name: 模型状态快照与Hook
description: 每版本状态 JSON、会话级最小加载及无 hook 后备入口
tags: [kind/guide, topic/mlops, entry]
---

# 模型状态快照与 Hook

> **本仓是公开框架分发仓**：`step-check`、`ledger`、`.dsh-hooks` 属私有源仓的每步核对机制，本仓未提供；本仓请用 `model-lab status` / `model-lab context`，hook 用 `.codex/hooks.json`。下文保留私有源仓原文作为决策背景。

每个模型版本目录的 `workflow-state.json` 是生成索引，供人和智能体快速判断
“这个版本当前处于哪一阶段、测点资料是否适用、仓库里有什么发布记录”。
事实仍以同目录 `model.yaml`、`decisions.yaml`、`input-bindings.yaml` 和
`validation/publications/` 的逐 Run 回执为准。不要手改 JSON，也不要把它
当作公司平台实时状态。

```powershell
& .\scripts\model-lab.ps1 context models/_drafts/<目录名> --task build
& .\scripts\model-lab.ps1 context models/_drafts/<目录名> --write
& .\scripts\model-lab.ps1 context --all --check
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
`& .\scripts\model-lab.ps1 verify-publication <model.yaml> --experiment <实验名> --run-id <Run ID> --company-package <公司ZIP>`：
它只读查询远端实验和 Run、下载核对固定实包并重载预测，全部通过后才在本地
补逐 Run 机器回执和状态 JSON；预测会执行该已验收模型并读取 IoT，失败不会
创建 Run 或上传制品。

`serving.point_metadata=not_applicable` 是数值入参模型的正常状态。测点数组
模型使用 `input-bindings.yaml` 保存设施名称、设备名称、数采点位编号、信号
编码、信号名称、应有信号和完整取数编码；状态 JSON 只放绑定文件路径、摘要
与完整度，不复制一套可能漂移的点表。老版绑定缺独立点位编号或信号编码时
会标 `legacy_code_only`，不是伪装成 2.1 已补齐。

## 步骤台账（ledger）的用法与分工

`workflow-ledger.json` 逐步骤登记 `status`、`owner` 与 `evidence` 指针（只追加、不改写，随工作区进 Git）。
它回答「这一步谁在做、自称做到哪、证据放哪」；`workflow-state.json` 仍是整体阶段与测点资料的生成索引。
两者都不当判据：每步的 `gate.verdict` 由 `step-check` 按步骤表重算，台账里的 `status` 只是声称。

```powershell
# 读台账（只读）；要给机器读时用 venv 形式接 --format json
& .\scripts\model-lab.ps1 ledger models/_drafts/<目录名>
.\.venv-311\Scripts\python.exe -m model_lab ledger models/_drafts/<目录名> --format json

# 登记一步（append-only）；status 六态：pending/in_progress/partial/done/blocked/skipped_external
& .\scripts\model-lab.ps1 ledger set models/_drafts/<目录名> --step 3 --status partial --owner <负责人> --evidence cmd:"<命令>"

# 复核声称与重算是否一致
& .\scripts\model-lab.ps1 ledger check models/_drafts/<目录名>
```

退出码语义：`ledger check` 返回 0＝台账与重算一致、1＝漂移（有步骤的声称与 `gate.verdict` 不符），
只作用于 `ledger` 子命令，不进入 `step-check` 的退出码（`step-check` 仍是 0＝本阶段无缺项、1＝有缺项、2＝不是模型工作区；加 `--strict` 时把已越过步骤的历史缺口也算 1），
也不改变任何门禁结论。`step-check` 输出里新增的 `ledger:<步骤>` 项是 advisory：只提示声称与重算的差异，不改变结论与退出码。
`--evidence` 三选一：`file:<路径>`、`cmd:<命令>`、`receipt:<路径>`；每一步只允许一个 owner，改口径靠追加新记录，不改旧记录。
`pending` 是台账的默认态（未开始），不用登记：`ledger set --status pending` 会被拒绝并返回 `ledger_status_pending_unsupported`，想表达「这一步还没产出」就让它保持未登记。

与每轮注入块的分工：`.dsh-hooks/context.md` 是 `step-check --write-context` 生成的**只读快照**，
每轮直接贴进上下文，只服务开局判断「卡在哪、下一步跑什么」，本身不落事实、不带历史；
ledger 才是仓库里的事实源，逐步骤留声称与证据指针，可 diff、可回溯。快照过期就重跑
`& .\scripts\model-lab.ps1 step-check <工作区> --write-context` 刷新；不要手改注入块，也不要把快照内容抄回 ledger。

仓库已交付只读 Codex hook；它在会话开始、压缩恢复和新提问时尝试定位明确的
模型 ID＋语义版本，提供最小文档指针。首次出现多个候选版本时应只问一次，
不能猜；会话焦点保存在本机临时目录，不进入 Git。Codex 需要先信任仓库 hook；
如果没有加载 hook，直接从 `AGENTS.md` 和本页进入，执行 `& .\scripts\model-lab.ps1 context`
与 `& .\scripts\model-lab.ps1 status`，流程仍完整。hook 既不上传，也不替用户回答业务决定。

仓库同时提供 DSH hook 配置（`.dsh-hooks/settings.json`）：`context` 在每次提问注入
「模型实验室 · 每步核对」块：块首第一行是产物落点，第二行是 `[开局]` 契约（先读状态、目标
没定只问一次、拿到答复再按 `& .\scripts\model-lab.ps1 context --task` 取最小文档），字符上限 1400。
`guard` 拦截往仓库根直接写文件（`write`、`edit` 与 `pwsh` 命令里的路径）。判据已从
「对原始文本跑正则」换成 `guard.workspaceRoot` 的**路径策略**（原来的 `guard.extraDeny`
正则已清空为 `[]`，见 ADR 0048）：把候选路径解析成绝对
路径（相对路径按会话 cwd），再判是否为仓库根的直接子级、名字是否在允许清单、是否命中
可疑后缀（`.md/.ipynb/.csv/.xlsx/.pkl`，与 `model_lab/root_discipline.py:50` 的 `STRAY_SUFFIXES` 大体同口径；hook 另把 `.json/.zip` 也算作根级可疑产物）。于是相对路径、带参数的
`Set-Content`、写窄后缀这三个老漏洞一并消失；带通配符的片段（`-p "test_*.py"`、
`Get-ChildItem *.csv`）与 `$` 开头的变量/成员访问（`$_.Line`、`$env:TEMP\x.txt`）都跳过，不会被
当成根目录新文件误拦（2026-10-19 重启后实测：`Set-Content -Path
.\x.csv`、`New-Item guard-probe2.txt`、`Set-Content -Path 'E:\...\x.md' -Value 1`
全部被拦，工作区内嵌套路径正常放行）。两点必须知道：注入的是文件快照，阶段推进后要重跑
`& .\scripts\model-lab.ps1 step-check <工作区> --write-context`；`guard` 仍只是提醒而不是门禁——它只挂
`pwsh/write/edit/read` 四个工具，`run_code` 里程序自己调 fs、bash/WSL 或外部编辑器都不
经过它，真正的越界判定是 `step-check` 输出里的「仓库根越界产物」行。项目 `settings.json`
写坏不再静默失效：`resolveSettings()` 会给出诊断（`HOOKS_KIT_DEBUG=1` 打到 stderr），
设置项热读，但策略代码随插件加载，改插件后需重启一次应用。`.\.venv-311\Scripts\python.exe -m model_lab setup --format json` 的 `hooks` 段会报每个客户端的启用状态，
其中 `clients.dsh` 给出 `guard_enabled`、`guard_rules`（`extraDeny` 条数，现为 0）、
`guard_root_rules`（`workspaceRoot.allow` 条数）与 `context_enabled`；`guard_enabled` 只要有
任一类判据就为真。

### 子会话的权限边界（2026-10-19 实测）

把流程分给子智能体（subagent / teammate）时要知道它们的权限可能比主会话窄：实测在部分
客户端里，子会话**没有 `models/_drafts/` 的写权限**（写探针文件报 `Access is denied`），
`wsl.exe` 也可能被直接拒绝（`Wsl/E_ACCESS_DENIED`）。症状是"命令本身没错却一直失败"，
不是工作区或仓库坏了。

约定：**只读取证、文档核对、门禁判读放子会话；`new --apply`、`advance --apply`、
`bundle-build`、`record-online-acceptance`、`publish` 这类写盘/远端步骤放主会话执行。**
子会话一旦发现写权限或 WSL 被拒，把结论和待执行的命令交回主会话，不要换路径反复硬试。

依据：[ADR 0018](../adr/0018-版本状态快照与会话级Hook.md)。
