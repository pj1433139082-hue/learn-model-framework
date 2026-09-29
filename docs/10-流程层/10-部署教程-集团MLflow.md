# 集团 MLflow 上传边界

本仓库只提供通用发布命令与验收规则，不含账号、公司 ZIP、真实实验和模型。先在私有模型目录完成 `status`、`validate`、`bundle-preflight`，再用明确的实验名与作者标签执行 `model-lab publish <model.yaml> --experiment <实验名> --owner <标签> --authorize-publish` 预览。用户已明确要求上传该版本且目标不变时，可加 `--company-package <私有ZIP> --apply`，无需重复仪式性确认。

只有获得 FINISHED Run、回载包摘要匹配且平台路径预测通过，才算 MLflow 上传成功。上传不等于业务平台参数绑定、人工任务调用、生产审批或定时调度。失败时保留旧 Run 与回执，查网络、签名、包或依赖原因，不把预览当成功。
