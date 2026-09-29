# 新模型开发 SOP

1. 在仓库外选定私有模型目录，先运行 `model-lab new <id> --name <名称> --owner <负责人> --models-root <目录>` 预览，再加 `--apply` 创建。模板生成的 `model.yaml` 是草稿，不代表已确认业务契约。
2. 用 `model-requirements-grill` 仅确认当前缺失的目的、入输出类型和单位、公式、异常、验收标准。测点模式还确认绑定与时间窗口。记录完整答案到 `decisions.yaml`，勿把“按推荐”原句写成答案。
3. 以 `model-data-audit` 核查数据和异常处理；以 `model-build-validate` 实现、从头执行 Notebook、运行固定输入回归，保存可复现的验证证据。真实数据不入本仓库。
4. 每阶段用 `model-lab status <工作区>` 看门禁，满足时用 `advance --apply`；不要直接改生命周期字段。
5. 在目标 Python 3.11 环境运行 `doctor`、模型测试、`bundle-build`、`bundle-preflight`。真实 IoT 和 MLflow 发布按 [运行环境 SOP](13-运行环境与MLflow制品SOP.md) 与指定模型的口径验收。

已发布版本的修改先用 `model-maintenance` 分类：纯运行环境补丁需保持行为基线，输入签名或公式变化须另立版本。不得覆盖旧版本记录。
