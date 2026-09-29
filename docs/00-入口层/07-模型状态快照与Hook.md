# 版本状态索引与 Hook

每个私有模型版本的 `workflow-state.json` 由 `model-lab context <工作区> --write` 从 `model.yaml`、绑定和回执重建。`--check` 只读报漂移；不要手改 JSON。`context --all` 需要 `--models-root` 或 `MODEL_LAB_MODELS_ROOT`，且该目录必须在框架仓库外。

Codex hook 只在本次会话已唯一定位模型时提示状态与最小文档；不会写模型、也不会在仓库内保存“当前模型”指针。没有配置私有目录、hook 不受信任或智能体不支持 hook 时，直接按 `AGENTS.md`、入口页、`context/status` 操作，流程不被阻断。
