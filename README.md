# Model laboratory framework

这是可供同事克隆的纯框架仓库；不含任何真实模型、厂站测点、数据、公司 ZIP 或发布回执。真实模型保存在各自有权限的私有目录，框架更新只在此仓库维护。Python 固定为 3.11。

## 从零开始（Windows PowerShell）

```powershell
git clone <本仓库的私有 GitHub 地址>
cd learn-model-framework
uv venv --python 3.11 .venv-311
uv pip install --python .\.venv-311\Scripts\python.exe -e ".[test]"
$env:MODEL_LAB_MODELS_ROOT = "D:\private-models"
.\.venv-311\Scripts\python.exe -m model_lab doctor
.\.venv-311\Scripts\python.exe -m model_lab new example-flow --name "示例流量" --owner example --models-root $env:MODEL_LAB_MODELS_ROOT
```

最后一条默认只预览；确认目标路径后加 `--apply`，草稿写入私有目录的 `_drafts/`，不会写入本仓库。Linux 可用 `uv venv --python 3.11 .venv-311`，再用 `.venv-311/bin/python` 执行相同模块命令。若 `uv` 尚未安装，可使用系统 Python 3.11 自带的 `venv`/`pip`；不要用 3.12 代替。私有目录应位于本仓库外，且不要放进另一个公开仓库。

先读 [入口层](docs/00-入口层/00-模型开发总览.md)。对某个已存在的版本，用 `model-lab context <私有工作区> --task <意图>` 得到最小文档集；用 `status` 查看阶段与缺口。`model.yaml` 是每个模型版本自己的契约文件，不是全局通用的已填配置；新建时由模板生成。测点、时间规则与特征处理必须按模型单独填写。

本机检查：`model-lab test-profile local` 和 `model-lab release-gate`。目标 Linux、真实 IoT、MLflow 上传要在私有模型任务中逐模型验收，不能把通用测试当作上线证明。公司包从批准的私有位置提供，不提交此库。
