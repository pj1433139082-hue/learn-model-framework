# 版本状态索引与 Hook

每个私有模型版本的 `workflow-state.json` 由 `model-lab context <工作区> --write` 从 `model.yaml`、绑定和回执重建。`--check` 只读报漂移；不要手改 JSON。`context --all` 需要 `--models-root` 或 `MODEL_LAB_MODELS_ROOT`，且该目录必须在框架仓库外。

Codex hook 只在本次会话已唯一定位模型时提示状态与最小文档；不会写模型、也不会在仓库内保存“当前模型”指针。没有配置私有目录、hook 不受信任或智能体不支持 hook 时，直接按 `AGENTS.md`、入口页、`context/status` 操作，流程不被阻断。

`context` 同时报告实际框架根目录、版本、Git 提交/修改状态与绝对文档指针。即使从私有模型目录调用，也按这些指针读取维护框架的文档，不从旧模型备份中的同名 `docs/` 猜入口。

生成状态索引不保存交互档位；任务续办另用 `task-plan <工作区> --task-id <任务ID>`。其 JSON 只保留任务、模型身份、框架版本/提交、档位、下一步与待答编号；不保留凭据、机器目录或永久远端授权。同一模型可有多个任务，必须显式选择任务 ID。当前事实仍以 `context/status` 重查为准。
