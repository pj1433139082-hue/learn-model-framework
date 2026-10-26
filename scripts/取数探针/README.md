# 取数探针（公开版）

这里是从私有模型仓剥离出的通用取数工具，不含厂站台账、真实凭据和内部联调记录。

## 组件

| 文件/目录 | 用途 |
| --- | --- |
| `iot-fetch-kit/` | 可移植的 IoT 历史数据批量取数包，直连 REST，不依赖公司密文 SDK |
| `iot_bulk_fetch.py` | 仓库内薄入口，自动找到同目录的 `iot-fetch-kit` |
| `iot_path_probe.py` | 网络路径探测：DNS/TCP/TLS、路由出口、是否 VPN；不需要凭据 |
| `iot_fetch_probe.py` | 真实 311 provider 取数探针；需要本机 `framework/` 下的公司 provider |
| `iot_ledger_to_nodes.py` | 台账 xlsx → 节点 JSON + Markdown 点表 |
| `plot_signals.py` | 对取数 parquet 的每个取用参数逐条出图 |
| `run_probe.ps1` | 一键运行网络路径探测和可选真实取数探测 |
| `build_kit.ps1` | 把上述脚本打成可带走的离线探针包 |

## 安装取数包

```powershell
& .\.venv-311\Scripts\python.exe -m pip install -e .\scripts\取数探针\iot-fetch-kit
```

或者只设置：

```powershell
$env:PYTHONPATH = (Resolve-Path .\scripts\取数探针\iot-fetch-kit).Path
```

## 凭据

凭据只从环境变量或本机忽略文件读取：

```powershell
$env:IOT_CLIENT_ID = '<client id>'
$env:IOT_CLIENT_SECRET = '<redacted>'
$env:IOT_TENANT = '<tenant>'
$env:IOT_TOKEN_URL = '<token endpoint>'
$env:IOT_API_URL = '<data endpoint>'
```

也可以创建 `iot-credentials.local.json`；它已在 `.gitignore` 中忽略，**绝不提交**。

## 安全边界

- `iot_path_probe.py` 不带凭据；
- `iot_bulk_fetch.py` / `iot-fetch-kit` 不打印 token 或 secret；
- 所有原始数据和台账不写进 Git；
- 公开仓不包含集团部署 kit 或 `model-lab deploy-kit` 命令。
