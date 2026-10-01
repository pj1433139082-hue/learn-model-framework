# 版本状态索引与 Hook

每个私有模型版本的 `workflow-state.json` 由 `model-lab context <工作区> --write` 从 `model.yaml`、绑定和回执重建。`--check` 只读报漂移；不要手改 JSON。`context --all` 需要 `--models-root` 或 `MODEL_LAB_MODELS_ROOT`，且该目录必须在框架仓库外。

Codex hook 只在本次会话已唯一定位模型时提示状态与最小文档；不会写模型、也不会在仓库内保存“当前模型”指针。没有配置私有目录、hook 不受信任或智能体不支持 hook 时，直接按 `AGENTS.md`、入口页、`context/status` 操作，流程不被阻断。

`context` 同时报告实际框架根目录、版本、Git 提交/修改状态与绝对文档指针。即使从私有模型目录调用，也按这些指针读取维护框架的文档，不从旧模型备份中的同名 `docs/` 猜入口。

生成状态索引不保存交互档位；任务续办另用 `task-plan <工作区> --task-id <任务ID>`。其 JSON 只保留任务、模型身份、框架版本/提交、档位、下一步与待答编号；不保留凭据、机器目录或永久远端授权。同一模型可有多个任务，必须显式选择任务 ID。当前事实仍以 `context/status` 重查为准。

## 操作完成但本地记录不完整

创建、分叉或状态推进已完成后，索引 I/O 写入失败会安全补写一次；不安全路径或无效事实不会重试。补写仍失败时命令返回 2，但保留原操作结果和可解析 JSON：`completion.operation=completed`、`completion.local_record=incomplete`、`completion.repeat_operation=false`，以及 `recovery.command` 参数列表。不要仅凭非零退出码重做创建、推进或上传。

先处理实际文件权限、磁盘、路径或事实问题，再通过固定启动器执行 `context <工作区> --write`，随后 `context <工作区> --check` 核对索引；不手改 JSON。若缺的是发布回执，仅重建索引不够，按 [上传边界](../10-流程层/10-部署教程-集团MLflow.md) 核验同一个 Run。恢复不增加用户审批，也不改变模型生命周期。见 [ADR 0019](../adr/0019-已完成操作与本地记录恢复分离.md)。
