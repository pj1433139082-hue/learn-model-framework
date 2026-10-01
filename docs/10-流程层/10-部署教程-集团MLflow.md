# 集团 MLflow 上传边界

本仓库只提供通用发布命令与验收规则，不含账号、公司 ZIP、真实实验和模型。先在私有模型目录完成 `status`、`validate`、`bundle-preflight`，再用明确的实验名与作者标签执行 `model-lab publish <model.yaml> --experiment <实验名> --owner <标签> --authorize-publish` 预览。用户已明确要求上传该版本且目标不变时，可加 `--company-package <私有ZIP> --apply`，无需重复仪式性确认。

只有获得 FINISHED Run、回载包摘要匹配且平台路径预测通过，才算 MLflow 上传成功。上传不等于业务平台参数绑定、人工任务调用、生产审批或定时调度。失败时保留旧 Run 与回执，查网络、签名、包或依赖原因，不把预览当成功。

指定 Run 已核验后，保存本地记录失败不否定上传：输出保留 `published` 或 `verified`，同时说明本地记录不完整，CLI 返回 2。不要因此再次 `publish --apply`。索引会在安全条件下补写一次，仍失败则按 `recovery.command` 修复；该字段是参数列表，不是可直接拼接执行的 shell 字符串。

如果回执缺失，使用固定框架启动器执行 `verify-publication <model.yaml> --experiment <同一实验名> --run-id <同一RunID> --company-package <私有ZIP> --format json`：它只读取核验既有 Run，并恢复本地记录，不创建实验或 Run、不再次上传。若原回执冲突或路径不安全，先排查冲突，保留原记录；核验不会覆盖它。回执已保存而仅索引缺失时，使用 `context <工作区> --write`。恢复结果与普通成功分别报告，见 [状态说明](../00-入口层/07-模型状态快照与Hook.md)。
