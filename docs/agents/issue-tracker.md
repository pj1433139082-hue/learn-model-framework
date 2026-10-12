# Issue tracker: GitHub

本仓库的规格与任务使用 GitHub 仓库 `pj1433139082-hue/learn-model-framework` 的 Issues，并通过 `gh` CLI 操作。

## Conventions

- 创建、读取、评论、标记和关闭任务均使用 `gh issue`。
- 从本地仓库的 `origin` 推断目标仓库，不在脚本中重复硬编码访问令牌。
- 规格作为父 Issue；实现任务各自使用独立 Issue。
- 任务依赖优先使用 GitHub 原生 issue dependencies；不可用时在正文写明 `Blocked by: #<number>`。
- 技能要求发布到 issue tracker 时，应创建 GitHub Issue。
- 技能要求读取任务时，应读取 Issue 正文、标签和评论。

## Pull requests as a triage surface

**PRs as a request surface: no.**

外部 PR 不自动进入需求分流队列；如将来需要，可直接修改本文件。
