# 模型实验室项目技能

本仓库把模型协作技能作为项目源码维护。唯一可编辑源位于
`.skillshare/skills/`；`.agents/skills/` 是本机同步结果，不进入 Git。

## 同事首次安装

在仓库根目录运行：

```powershell
skillshare status --json
skillshare sync --dry-run --json
skillshare sync --json
```

同步目标固定为仓库内 `.agents/skills/`，符合 Codex 的项目技能目录约定。完成后
重新打开一个 Codex 任务，使技能列表重新加载。更新仓库后再次运行 `skillshare
sync --json` 即可取得新版技能。

如果 PowerShell 提示找不到 `skillshare`，先核对**是否只是命令未被当前终端
解析到**，不要立刻判定未安装或重复安装。Windows 的一种安装位置是
`%LOCALAPPDATA%\Programs\skillshare\skillshare.exe`；确认文件确实存在后，
可以用其完整路径执行上面相同的 `status`、`sync --dry-run` 和 `sync` 命令。
受限智能体环境可能读不到安装目录，此时应报告“路径未核实”，不能报告
“软件未安装”。确实未安装时再按团队批准的软件渠道安装。不要手工复制到
个人 `superpowers`、`.codex/skills` 或其他目录来制造第二份事实源。

## 使用入口

使用者不需要记住所有技能名称，可以直接说：

```text
@docs/00-入口层 阅读，接管这个模型任务：<任务描述>
```

`model-lab-router` 会按任务调用需求设计、数据审查、构建验证、运行时发布或在役
模型维护技能。显式 `$model-lab-router` 只是可选入口。
仓库另提供 `.codex/hooks.json` 辅助恢复会话级模型焦点；它不属于 Skillshare
生成目录，需要在 Codex 的 `/hooks` 中审核并信任。未信任或客户端不支持时，
`AGENTS.md`、入口文档与 `model-lab` 的 `context`/`status` 仍可完整使用；参见
[`07-模型状态快照与Hook.md`](../00-入口层/07-模型状态快照与Hook.md)。

## 与本仓、分发仓的关系

本仓 `.skillshare/skills/` 是七个 `model-*` 技能的唯一事实源（五个场景技能 +
`model-lab-setup` 环境自检 + `model-lab-router` 指针）。分发仓
`learn-model-framework` 发布同一批技能：编辑后必须在该仓再跑一次
`skillshare sync --json`，否则 `tests/test_repository_layout.py` 的
`test_skills_match_framework_distribution_exact` 会直接报出哪一份不一致（测试按
本仓目录取全集，两个仓库的技能目录集合与每个 `SKILL.md` 的正文都要逐行相同）。

两份仓库的技能正文保持一致，但它们的自检口径可以不同：分发仓的
`model_lab/setup.py` 用固定名单核对技能哈希，本仓的 `model_lab/skill_contract.py`
按合同行核对「技能 → workflow / step」的落点。改技能时两边都要跑一遍自检。

## 维护与检查

只修改 `.skillshare/skills/<skill>/SKILL.md`。修改后依次执行技能快速校验、
`skillshare sync --dry-run --json` 和 `skillshare sync --json`。技能不得保存长期
凭据、不得隐含真实发布授权，也不得要求同事安装 Ask Matt 才能使用。
