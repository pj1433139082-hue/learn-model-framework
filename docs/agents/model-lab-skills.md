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

## 使用入口

使用者不需要记住所有技能名称，可以直接说：

```text
@docs/00-入口层 阅读，接管这个模型任务：<任务描述>
```

`model-lab-router` 会按任务调用需求设计、数据审查、构建验证、运行时发布或在役
模型维护技能。
