## 变更描述 (Summary & Motivation)

<!-- 请简要说明此 PR 解决的问题、引入的新特性或技术方案。包含相关的背景动机与实现细节。 -->

## 关联 Issue (Related Issue)

<!-- 请使用 GitHub 关键字自动关联或关闭对应 Issue，例如： -->
- Closes #
- Fixes #

## 变更类型 (Type of Change)

- [ ] 🐛 **Bug fix** (修复代码错误或异常行为)
- [ ] ✨ **New feature** (新增功能、CLI 命令或工作流能力)
- [ ] 🧩 **Model Adapter** (新增模型算法模板、协议契约或科学计算适配)
- [ ] 📝 **Documentation / ADR** (文档、注释、教程或架构决策记录更新)
- [ ] ♻️ **Refactoring** (代码重构与结构优化，不改变外部功能)
- [ ] 🧪 **Tests** (新增或完善单元测试与门禁用例)
- [ ] 🔧 **CI / Build** (构建脚本、工作流或工程化基础设施变动)

## 变更自查清单 (Checklist)

在提交此 Pull Request 之前，请确认已逐项自检：

- [ ] **Python 3.11 兼容性**：代码已在 Python 3.11 环境下测试，类型注解完备无语法错误。
- [ ] **单元测试完备性**：新增或修改的代码逻辑在 `tests/` 下有对应的单元测试覆盖。
- [ ] **门禁检查通过**：本地运行框架发布门禁无报错：
  - Windows: `& .\.venv-311\Scripts\python.exe -m model_lab release-gate`
  - Linux: `bash scripts/model-lab.sh release-gate`
- [ ] **全量测试通过**：本地运行全量测试套件（`python -m unittest discover -s tests`）100% 绿灯。
- [ ] **🛡️ 零泄漏与脱敏红线**：
  - [ ] 确认**绝无**任何真实水务/工业生产厂站名称或内部代号；
  - [ ] 确认**绝无**任何实际生产测点编码或物理设备点位；
  - [ ] 确认**绝无**未经脱敏的真实权重、私有数据包、密码、Token 或内部内网 IP；
  - [ ] 所有示例与测试均使用框架内置的虚构点位（如 `PSXXXX` / `TEST-001`）与合成数据。
- [ ] **文档与决策同步**：相应的用户文档、命令行使用指南或 ADR 决策记录已同步更新。
