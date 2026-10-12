# 模型工作区说明

本目录为算法模型工作区。

## 样板模型
- `sample-water-model`: 通用算法框架的公开样板工程，展示标准契约（`model.yaml`）、点位映射（`input-bindings.yaml`）、架构决策（`decisions.yaml`）及单元测试规范。

## 研发工作流
新模型请使用脚手架创建：
```powershell
model-lab new <model-id> --name "<模型名称>" --owner "<责任人>" --models-root models --apply
```
新建模型草稿将存放在 `models/_drafts/<model-id>/` 目录下。
