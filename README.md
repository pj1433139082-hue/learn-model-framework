# 模型实验室框架

同事克隆、更新本仓库的 `main` 即可。这里保存框架代码、技能、SOP、空模板和虚构测试；真实模型、厂站测点、数据、公司包及发布回执保存在仓库外私有目录。Python 使用 3.11。

## 首次使用

从仓库的 Code 按钮复制克隆地址，克隆后在 Windows PowerShell 执行：

```powershell
cd learn-model-framework
& .\scripts\bootstrap.ps1 -ModelsRoot "D:\private-models"
```

这一步检测或创建 `.venv-311`，安装框架和测试依赖，记住私有模型目录，并同步本项目技能。已有 Python 3.11 环境会被复用；找不到 `uv` 时尝试系统 Python 3.11，确实缺少时显示安装指引。本机目录保存在忽略的 `.model-lab.local.json` 中，不提交 Git。初始化不会创建真实模型或访问公司服务。

依赖安装访问公网，使用 HTTP 代理时先在终端设置 `HTTP_PROXY` / `HTTPS_PROXY`；助手继承设置，不改动系统代理或 VPN。

Linux 使用 `bash scripts/bootstrap.sh --models-root /path/to/private-models`；系统需提供 Bash、`uv` 或 Python 3.11。

Codex 是否信任 hook 由客户端控制。初始化只报告 hook 文件和后备入口，不能代替客户端信任操作。如果 Skillshare 不可用，先直接读 `.skillshare/skills/model-lab-router/SKILL.md`；基础命令仍可使用。[手工配置与技能说明](docs/00-入口层/04-人工操作快速开始.md)

## 日常任务

在本框架目录打开智能体，直接说：

```text
@docs/00-入口层 阅读，接管这个模型任务：<模型 ID / 版本和任务描述>
```

Windows 的统一命令入口是 `scripts/model-lab.ps1`，Linux 是 `bash scripts/model-lab.sh`。它们固定使用本框架的 Python 与隔离导入；即使当前目录有旧框架副本，也不会因工作目录而加载旧代码。

```powershell
& .\scripts\model-lab.ps1 doctor
& .\scripts\model-lab.ps1 new example-flow --name "虚构流量模型" --owner example
# 上一条只预览；确认路径后加 --apply 创建虚构草稿
& .\scripts\model-lab.ps1 context "D:\private-models\_drafts\example-flow"
& .\scripts\model-lab.ps1 status "D:\private-models\_drafts\example-flow"
```

不需要每次再传 `--models-root`；命令参数优先于环境变量，环境变量优先于本机配置。`model.yaml` 是每个模型版本自己的契约，创建时由模板生成。测点、时间窗口、异常处理与算法按模型填写；不能复制其他模型的配置当成已确认事实。

`new --apply` 只创建待完善的草稿，不是模型已建成或上传；`status` 会列出真实业务缺项，智能体再按场景集中询问，不逐命令确认。

任务的全自动、半自动、逐步确认档位只选一次。恢复时读取该任务计划和模型状态；仅业务条件变化或出现新的必填问题时集中询问。模型上传仍按你指定的实验名和版本执行。[入口与任务分流](docs/00-入口层/00-模型开发总览.md)

## 获取更新

同事日常留在 `main`，更新时执行：

```powershell
git pull --ff-only
& .\scripts\bootstrap.ps1 -Update
```

Linux 的第二步为 `bash scripts/bootstrap.sh --update`。第二步同步依赖、技能与本机设置，记录更新前后框架提交；不再询问已有的私有目录，也不修改模型契约或验收证据。若 Git 提示本地冲突，先处理冲突，勿用强制覆盖。模型任务通过任务计划记录实际框架提交；新建草稿也保存创建时框架来源。

出现问题时，先用 `doctor` 核对实际框架来源和 `.model-lab/setup-state.json` 中的前后提交。需要回退时，在新的目录克隆仓库、切到记录的旧提交、重新初始化，并继续指向原私有目录；不要重置或删除原模型。[手工更新与回退](docs/00-入口层/04-人工操作快速开始.md)

框架本机检查使用 `scripts/model-lab.ps1 test-profile local` 和 `scripts/model-lab.ps1 release-gate`。通用测试不能证明真实模型已上线；公司包、目标 Linux、真实取数与 MLflow 发布仍在私有模型任务中逐模型验收。[运行环境与发布](docs/10-流程层/13-运行环境与MLflow制品SOP.md)

测试也要看 `status`、`complete` 与跳过原因：退出 0 的 `partial` 仍不是零跳过通过。例如 Windows 缺符号链接权限的用例可能跳过，须明确记录并在具备条件的环境补测。
