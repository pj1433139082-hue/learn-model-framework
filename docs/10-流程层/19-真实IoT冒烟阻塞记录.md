# 19 真实 IoT 冒烟记录（公开脱敏）

> 本页只保留公开可复现的验证口径。具体厂站、测点、内部工单和联调记录不随公开仓分发；需要完整案例时向内部仓库负责人索取。

## 公开验证口径

公开框架提供 Windows 网络诊断档：

```powershell
$env:MODEL_LAB_REAL_IOT = '1'
$env:MODEL_LAB_COMPANY_IOT_ZIP = '<外部公司 ZIP>'
$env:MODEL_LAB_SMOKE_START = '<最近 2 小时内的 Asia/Shanghai 时间>'
& .\scripts\model-lab.ps1 test-profile windows-network
```

通过判据：

- 公司 ZIP 的 SHA-256 与审核记录一致；
- 本机 `framework/` 下的 311 provider 与 ZIP 内文件逐字节一致；
- 真实取数返回非空 DataFrame，包含 `data_time` 和请求的两个测点列；
- 最新值有限且非负；
- 结果只证明 Windows 本机网络与取数通，不替代目标 Linux 上的模型验收。

## 限制

- 该档是 Windows 诊断，不是发布验收；
- 真实发布、在线验收和平台绑定仍按项目发布通道执行；
- 不记录真实凭据、厂站台账或原始数据。
