# 贡献指南 (Contributing Guide)

感谢你对 `learn-model-framework` 的关注与贡献！

作为通用工业与水务模型实验室脚手架，我们致力于提供高复用、契约化、可自动化验证的机器学习与机理模型研发基础设施。

在参与贡献前，请先阅读我们的 [行为准则 (CODE_OF_CONDUCT.md)](CODE_OF_CONDUCT.md) 与 [安全政策 (SECURITY.md)](SECURITY.md)。

---

## 1. 架构定位与两库隔离原则

在深入代码之前，请理解本项目的核心架构原则：

1. **公开通用框架 vs 私有业务资产**：
   - 本仓库仅包含通用的模型生命周期管理、统一 CLI、标准化目录脚手架、推参验证规范与合成测试数据；
   - 真实的生产模型文件、现场测点、生产数据集及业务凭据**绝不进入本仓库**，保存在仓库外的私有目录（`models-root`）；
2. **零泄漏红线**：
   - 所有的单元测试、文档示例与模板必须使用虚构测点（如 `PSXXXX`、`TEST-001`）与合成数据，严禁引入真实工厂点位。

---

## 2. 本地开发环境搭建

框架要求使用 **Python 3.11**。

### 2.1 克隆仓库

```bash
git clone https://github.com/pj1433139082-hue/learn-model-framework.git
cd learn-model-framework
```

### 2.2 初始化开发环境

我们提供了跨平台的引导脚本，会自动创建或配置 Python 3.11 虚拟环境（`.venv-311`）、安装开发依赖并初始化本地技能体系。

- **Windows (PowerShell)**:
  ```powershell
  & .\scripts\bootstrap.ps1 -ModelsRoot "$env:TEMP\test-models"
  ```
- **Linux / macOS (Bash)**:
  ```bash
  bash scripts/bootstrap.sh --models-root "$RUNNER_TEMP/test-models"
  ```

> **提示**：若你在内网或需要代理访问 PyPI/GitHub，请在执行前设置环境变量：
> ```powershell
> $env:HTTP_PROXY = 'http://127.0.0.1:7897'
> $env:HTTPS_PROXY = 'http://127.0.0.1:7897'
> ```

### 2.3 验证环境可用性

```powershell
# 运行环境自检
& .\scripts\model-lab.ps1 doctor
# 或使用 Python 模块入口
& .\.venv-311\Scripts\python.exe -m model_lab doctor
```

---

## 3. 开发流程与提交规范

### 3.1 分支管理

- 从最新的 `main` 分支切出功能分支进行开发：
  - 功能特性：`feat/<feature-name>`
  - 缺陷修复：`fix/<bug-name>`
  - 文档改进：`docs/<topic-name>`
  - 重构优化：`refactor/<refactor-name>`

### 3.2 Commit 命名规范

提交信息请遵循 [Conventional Commits](https://www.conventionalcommits.org/) 规范，保持清晰简明：

- `feat`: 新增功能特性（如新增模型适配器、扩展 CLI 参数）
- `fix`: 修复缺陷或异常
- `docs`: 文档、注释或 ADR 更新
- `style`: 代码格式调整（不改变逻辑）
- `refactor`: 重构代码（无新特性也无缺陷修复）
- `test`: 增加或修改测试用例
- `chore`: 构建系统、依赖更新或辅助工具变动

**示例**：
```text
feat(adapter): 新增通用机理修正模型协议模板与推参验证
fix(cli): 修复 Windows 环境下模型草稿路径包含空格时的解析异常
docs(adr): 补充 ADR 0023 多点位推参服务契约说明
```

---

## 4. 门禁检查与测试规范 (Gates & Testing)

在提交任何代码前，**必须在本地 100% 跑通以下门禁检查**：

### 4.1 发布准入门禁（Release-Gate）

发布门禁会严密扫描整个工作树与 Git 索引，校验文件白名单、排除潜在私有信息泄露：

- **Windows**:
  ```powershell
  & .\.venv-311\Scripts\python.exe -m model_lab release-gate
  ```
- **Linux**:
  ```bash
  bash scripts/model-lab.sh release-gate
  ```
*输出必须为 `framework release-gate: PASSED`。*

### 4.2 隐私测试门禁

专项校验脱敏规则与白名单逻辑：

```powershell
& .\.venv-311\Scripts\python.exe -m unittest tests/test_privacy_gate.py
```

### 4.3 全量单元测试

全量测试套件保证功能逻辑完整性：

```powershell
& .\.venv-311\Scripts\python.exe -m unittest discover -s tests
```

---

## 5. 提交 Pull Request

1. 将你的分支推送到你的 Fork 仓库；
2. 在 GitHub 上向 `pj1433139082-hue/learn-model-framework` 的 `main` 分支发起 Pull Request；
3. PR 标题使用 Conventional Commits 格式；
4. 认真填写 PR 模板（`.github/pull_request_template.md`），逐项勾选自查清单；
5. GitHub Actions CI 将自动运行环境验证与测试（`verify.yml`），请确保所有 CI checks 均呈绿灯；
6. 维护者将进行 Code Review 并提供反馈，经审查通过后即可合入。
