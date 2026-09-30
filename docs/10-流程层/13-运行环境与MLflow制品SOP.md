# Python 3.11 与 MLflow 制品

本框架仅支持 Python `>=3.11,<3.12`。在目标 OS/架构上安装 3.11 和本项目依赖；公司提供的 IoT 模块、PyArmor 原生运行时、公司 ZIP 与凭据必须通过私有渠道提供，不能提交框架仓库。Python 解释器不打进模型包；包中的环境声明与目标 ABI 要一致。私有模型的 `serving.provider` 记录审核后的包摘要，构建时校验实际 ZIP 字节；共享框架不固定某一家公司的包摘要。

先运行 `model-lab doctor` 和 `test-profile local`。模型专属 Linux 离线/在线档需要私有测试模块 `MODEL_LAB_PRIVATE_TEST_MODULE`、经核验的 `MODEL_LAB_COMPANY_IOT_ZIP`，在线档还需 `MODEL_LAB_REAL_IOT=1`、明确的 `MODEL_LAB_SMOKE_START`。通用仓库没有真实测点，缺任一前提就应显示 BLOCKED，绝不报成通过。Windows 网络诊断不能代替 Linux 3.11 验收。

从维护框架的统一 launcher 运行 `doctor`，默认检查实际框架而非调用时的工作目录，并显示框架源、版本、提交与修改状态。可用 `doctor --company-package <外部ZIP> --target-platform linux_x86_64` 核对目标包；也可用环境变量 `MODEL_LAB_COMPANY_IOT_ZIP` 或本机忽略配置中的 `company_iot_zip` 保存路径。检查只读 ZIP 和原生头部，不导入公司模块、不访问内网；`files_verified` 仅说明静态结构匹配，不能当作取数成功。未提供公司包不阻塞框架初始化或通用本机回归。

需要接入既有模型的私有基线和测试时，先参考 [完全合成的私有扩展示例](17-私有扩展接入示例.md)，把包放在框架外并安装到所用解释器环境。合成示例仅验证安装与协议，不能冻结实际模型的行为基线或发布结论。

`bundle-build <私有工作区> --sample-csv <私有样本>` 默认只预览，`--apply` 后才构建。`bundle-preflight` 检查 MLflow `service/` 的 Python、签名、依赖与制品。点位模型需按[在线验收回执契约](16-在线验收回执契约.md)完成模型专属真实测试。私有部署配置一次性提供 `MODEL_LAB_ALLOWED_TRACKING_HOST` 和 `MODEL_LAB_ALLOWED_ARTIFACT_HOST`，上传器核对公司包实际连接目标；这不是每次向使用者索要的新信息。真实发布前固定目标实验、模型版本和包摘要；`publish --apply` 只在明确指定的上传任务内执行，上传后必须回载同一 Run 和服务包验证。业务平台绑定/任务调用与自动调度是独立活动，不能由成功上传推定。
