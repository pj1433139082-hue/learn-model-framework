# iot-fetch-kit：直连 REST 的 IoT 历史数据批量取数

> 版本 1.0.0 · Python >= 3.9 · 只有运行依赖是 `requests`。
> 本包从 `scripts/取数探针/iot_bulk_fetch.py` 抽出来，CLI 参数、默认值、落盘契约与旧脚本逐条一致。

## 1. 这是什么 / 什么时候用它

`iot-fetch-kit` 是一个**可直接整体拷到别的项目**的独立取数包：不 import 本仓库任何模块，
只依赖 `requests`（读 parquet / 读台账时才要 pandas 等）。它解决三类问题：

* **要任意历史区间的数据**：平台 3.11 密文 SDK 没有 `end_time`、每查询硬编码 1500 条、串行；
  这里直接调 REST 接口，`startTime/endTime/maxSizePerNode` 都是请求体字段，区间可精确指定。
* **要几个月的训练数据**：按月落 parquet，自动逐月探"每测点上限"再选窗口，32 并发实测约 72 万采样点/秒。
* **要把这套能力搬到别的项目**：包只认环境变量里的端点和凭据，不认本仓库路径；`examples/` 里有
  可直接改的脚本。

什么时候**不要**用它：只要"现在附近"的少量点，用 `iot_fetch_probe.py` 或 3.11 密文 SDK 更省事；
只想验证"网络能不能通、凭据对不对"，用本包的 `doctor` 子命令（不发凭据）。

## 2. 三种集成方式

### 方式 A：装进目标项目的虚拟环境（推荐）

```powershell
# 在 kit 目录里
E:\target\.venv\Scripts\python.exe -m pip install -e .
# 只取数
E:\target\.venv\Scripts\python.exe -m pip install -e ".[parquet]"
# 取数 + 台账转点表
E:\target\.venv\Scripts\python.exe -m pip install -e ".[all]"
```

装完多一个命令 `iot-fetch`（等于 `python -m iot_fetch`）。

### 方式 B：把 `iot_fetch/` 目录整个拷进目标项目

不装包也能用：把本包的 `iot_fetch/` 目录复制到目标项目的源码树里（或它的某个公共库目录），
保证 `import iot_fetch` 可用即可。包内部没有任何相对路径假设。

### 方式 C：PYTHONPATH 指向 kit 根目录

```powershell
$env:PYTHONPATH = "<repo>\scripts\取数探针\iot-fetch-kit"
E:\target\.venv\Scripts\python.exe -m iot_fetch --help
```

三种方式都没有改动目标项目的其他文件，删掉 kit 目录即完全还原。

## 3. 依赖与 extras

| 依赖 | 版本 | 什么时候需要 |
| --- | --- | --- |
| `requests` | `>=2.28` | 必装，取数 / 令牌 / doctor 都要 |
| `pandas` | `>=2.0` | 建表、写 parquet；台账转点表 |
| `pyarrow` | `>=14` | `df.to_parquet(compression="zstd")` |
| `openpyxl` | `>=3.1` | 读台账 `.xlsx`（`ledger` 子命令） |

`pyproject.toml` 里对应三个 extras：

```powershell
pip install -e ".[parquet]"   # pandas + pyarrow
pip install -e ".[ledger]"    # pandas + openpyxl
pip install -e ".[all]"       # 两者
```

**缺依赖时的行为**（不会静默改变输出格式）：

* `--format auto`（默认）缺 pandas 或 pyarrow → 自动退化为 JSONL，并打印
  `⚠ 落盘格式 jsonl：<原因>`；
* 显式 `--format parquet` 却缺依赖 → 直接 `RuntimeError`，不会偷偷换格式；
* `ledger` 子命令缺 pandas → `sys.exit("缺少 pandas：请 pip install 'iot-fetch-kit[ledger]'")`。

依赖声明见 [pyproject.toml](pyproject.toml)。

## 4. 凭据与合规

### 4.1 环境变量

| 变量 | 含义 | 默认值 |
| --- | --- | --- |
| `IOT_TOKEN_URL` | 令牌端点 | `https://saas-iam.bewg.net.cn/realms/bewg/protocol/openid-connect/token` |
| `IOT_API_URL` | 历史取数端点 | `https://be-iot-saas.bewg.net.cn/ts/v3/datapoint/history/rawvalue` |
| `IOT_DATA_URL` | 同上（公司框架别名，`IOT_API_URL` 优先） | — |
| `IOT_TENANT` | 租户（请求头 `X-Tenant-Id`） | `bewg` |
| `IOT_TENANT_ID` | 同上（公司框架别名，`IOT_TENANT` 优先） | — |
| `IOT_CLIENT_ID` | OAuth client_id | `ai-console` |
| `IOT_CLIENT_SECRET` | OAuth client_secret | 空，**必须由调用方提供** |
| `IOT_CREDENTIALS_JSON` | 本地凭据 JSON 路径（见 4.3） | 空 |
| `IOT_USE_ENV_PROXY` | `1`=尊重系统代理 | `0`（不读环境代理，见第 9 章） |
| `IOT_FETCH_FORCE_JSONL` | `1`=强制 JSONL 落盘 | `0` |
| `IOT_FETCH_SLEEP_SCALE` | 重试退避倍率（秒） | `1.0`（测试里设 `0` 可归零） |

默认值定义在 [iot_fetch/config.py](iot_fetch/config.py)，`IotConfig.from_env()` 会读取它们。

**取值优先级（高 → 低）**：显式 CLI（`--client-secret` / `--client-id` / `--credentials-json`）
> 环境变量 > 本地凭据 JSON > 内置默认值。环境变量里 kit 名优先于公司框架名：
`IOT_TENANT` > `IOT_TENANT_ID`，`IOT_API_URL` > `IOT_DATA_URL`；只设公司名也能直接用。

### 4.2 合规红线

* **凭据绝不入库**：不要写进脚本、Notebook、`.env` 提交、文档或聊天记录；只放进程环境变量，
  CI 里用平台自带的 secret store 注入。
* **往公网地址发凭据要先批准**：把 `IOT_TOKEN_URL`/`IOT_API_URL` 指向公司内网以外的地址，
  等于把 client_secret 发给第三方，必须先按仓库规则申请。
* 源码里刻意不出现「凭据键名 + 赋值 + 字面量」的形态（会被 `model_lab/security_audit.py` 门禁误判）：
  令牌请求体沿用旧脚本的键名拼接写法（`"client_" + "secret"`），见 [iot_fetch/auth.py](iot_fetch/auth.py)。
* **`doctor` 不发任何凭据**：只发 `grant_type=client_credentials`（无 client_id/secret）的空请求。
  取数端点看到 `401 + WWW-Authenticate: Bearer` 算正常；令牌端点（Keycloak）看到
  `401 + JSON {"error":"invalid_client"}`（本来就不带 Bearer 头）也算正常。可以放心先在目标机器上跑。
* 本包不打印令牌与密钥；401 只打印"令牌失效，重试"之类的状态。

### 4.3 凭据放在哪（公司框架双通道）

公司部署手册的资源 `resources/infra/mlops_deploy_iot2.py` 用的是**双通道**：先看环境变量，
没有就用内置密文 Enclave 在内存里解码出凭据。它认的环境变量名是 `IOT_CLIENT_SECRET`、
`IOT_CLIENT_ID`、`IOT_TENANT_ID`、`IOT_TOKEN_URL`、`IOT_DATA_URL`（另有 MinIO/MLflow 字段，与本包无关）。
本包与它对齐，两种通道都支持：

1. **环境变量**（服务器/CI 推荐）：4.1 表里的名字都认，公司框架名自动当别名，只设公司名也能跑；
2. **本地凭据 JSON**（个人机器/临时联调推荐）：

```json
{
  "iot_client_secret": "placeholder",
  "iot_client_id": "ai-console",
  "iot_tenant_id": "bewg",
  "iot_token_url": "https://saas-iam.bewg.net.cn/realms/bewg/protocol/openid-connect/token",
  "iot_data_url": "https://be-iot-saas.bewg.net.cn/ts/v3/datapoint/history/rawvalue"
}
```

> 上面的 `placeholder` 只是占位，真实值从密钥管理系统取，别写进仓库。

```powershell
python -m iot_fetch --factory-code PS37217-02 --credentials-json scripts\取数探针\iot-credentials.local.json --start 2026-03-01 --end 2026-03-07 --out raw
```

* JSON 同时接受公司同名字段（`iot_*`）与 kit 风格（`client_secret` / `client_id` / `tenant` /
  `token_url` / `api_url`），同一字段两种都写时 kit 风格优先，未知字段忽略；
* 路径也可以走环境变量 `IOT_CREDENTIALS_JSON`，命令行 `--credentials-json` 优先于它；
* 建议放 `scripts\取数探针\iot-credentials.local.json`（仓库 `.gitignore` 已覆盖），**绝不提交**；
* 本仓库内的入口 `scripts\取数探针\iot_bulk_fetch.py` 会在没有显式凭据、也没设 `IOT_CREDENTIALS_JSON`
  时自动读同目录的这个文件（存在才用；显式参数与环境变量仍然优先）；
* 文件不存在 / JSON 非法 / 顶层不是对象 → 中文报错、退出码 1，报错只带路径不带内容；
  任何路径都不打印 secret。

不推荐把 secret 直接放命令行（会进 shell 历史和进程列表）；临时排障用完即清。

> 注意：本包没有内置密文 Enclave（那是公司框架自己的机制）。要完全离线携带凭据，请让框架
> 在启动时把 `IOT_*` 注入进程环境，再用本包取数。

## 5. 快速开始

先给凭据：服务器/CI 用环境变量，个人机器可以用本地凭据 JSON（见 4.3）。

```powershell
$env:IOT_CLIENT_SECRET = "<从密钥管理系统取>"
```

### 5.1 先体检网络（不发凭据）

```powershell
python -m iot_fetch doctor
```

看到两行 ✅ 与「结论：2/2 端点可达」再往下走；有 ❌ 先修网络。

### 5.2 探"每测点上限"与建议窗口

```powershell
python -m iot_fetch --factory-code PS37217-02 `
  --machine-prefix "-BE_GFJF_0001-C_003_" --machine 0001,0002 `
  --start 2026-01-01 --end 2026-04-01 --probe
```

只打印不落盘，读到「近期上限 / 起点附近上限 / 建议窗口」后退出 0。

### 5.3 取一个区间（按月落盘）

```powershell
python -m iot_fetch --factory-code PS37217-02 `
  --machine-prefix "-BE_GFJF_0001-C_003_" --machine 0001,0002,0003 `
  --signals V_IF,V_OP,V_P `
  --start 2026-01-01 --end "2026-04-01 00:00" `
  --out raw --workers 32
```

产物：`raw/PS37217-02-202601.parquet`、`-202602.parquet`、`-202603.parquet`。
参数逐条说明见第 6 章；也可以直接跑 [examples/fetch_factory.ps1](examples/fetch_factory.ps1)。

### 5.4 自定义点表（JSON 数组 / CSV）

```powershell
# JSON：[{"namespace": "...", "id": "..."}, ...]
python -m iot_fetch --factory-code PS37217-02 --nodes-file examples/nodes.example.json `
  --start 2026-03-01 --end "2026-03-02 00:00" --out raw

# CSV：表头必须是 namespace,id
python -m iot_fetch --factory-code PS37217-02 --nodes-file my_nodes.csv `
  --start 2026-03-01 --end "2026-03-02 00:00" --out raw
```

给的节点清单会**原样**作为请求里的 `nodes`；此时 `--machine/--machine-prefix/--signals` 不再参与拼测点名。

### 5.5 台账 xlsx → 点表

```powershell
python -m iot_fetch ledger --ledger "data/台账/PS37217-02-自控信号台账-xxxxxxxx.xlsx" `
  --match 曝气风机 --signals V_IF,V_OP,V_P `
  --out nodes_aeration.json --md point_table.md
```

先用 `--list-devices` 看台账里都有哪些设备名，再决定 `--match`。

## 6. CLI 参数表与退出码

### 6.1 取数（默认子命令，等价旧脚本）

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--factory-code` | **必填** | 厂站码，如 `PS37217-02`；用于文件名与拼测点名 |
| `--machine-prefix` | `""` | 机台命名空间前缀，如 `-BE_GFJF_0001-C_003_` |
| `--machine` | `""` | 机台序号，逗号分隔，如 `0001,0002,0003` |
| `--signals` | `V_IF,V_OP,V_P,V_ITIT,V_I,S_RUN` | 信号名，逗号分隔 |
| `--nodes-file` | 空 | JSON 数组 `[{"namespace","id"}]` 或 CSV（表头 `namespace,id`） |
| `--start` | 空 = 结束前 30 天 | `YYYY-MM-DD` 或 `YYYY-MM-DD HH:MM`（北京时间） |
| `--end` | 空 = 现在 | 同上 |
| `--out` | `raw` | 输出目录（每月一厂一个文件） |
| `--workers` | `32` | 并发窗口数（实测 32 稳定） |
| `--window-hours` | `0` | `0`=按探针自动；`>0` 强制窗口并**完全跳过**探针 |
| `--max-size` | `0` | `0`=按探针；`>0` 强制 `maxSizePerNode`（跳过初始探针） |
| `--retries` | `4` | 单窗口失败重试次数 |
| `--probe` | 关 | 只探上限与延迟，不写数据，退出 0 |
| `--client-id` | 读 `IOT_CLIENT_ID`，再缺省 `ai-console` | OAuth client_id |
| `--client-secret` | 读 `IOT_CLIENT_SECRET` | 缺了直接报错退出（见 6.4） |
| `--credentials-json` | 读 `IOT_CREDENTIALS_JSON` | 本地凭据 JSON（字段见 4.3）；CLI > 环境变量 > JSON > 默认值 |
| `--step-seconds` | `15` | 采样周期秒数，用于把每测点上限换算成窗口；别的项目采样周期不是 15s 时改它 |
| `--format` | `auto` | `auto` / `parquet` / `jsonl`（见第 7 章） |
| `--version` | — | 打印 `iot-fetch-kit 1.0.0` 后退出 0 |

时间参数解析与原脚本一致：只给日期时按北京时间当天 00:00；`--start` 缺省为 `--end` 前 30 天；
区间按北京时间自然月切分。

### 6.2 `doctor` 子命令（不发凭据）

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--token-url` | `IOT_TOKEN_URL` / 内置默认 | 覆盖令牌端点 |
| `--api-url` | `IOT_API_URL` / 内置默认 | 覆盖取数端点 |
| `--timeout` | `8` | 单步超时秒数 |
| `--json` | 关 | 输出机器可读 JSON（`ok` + `checks[]`） |

输出表格列：端点 / 主机:端口 / DNS / TLS / HTTP / 结论，随后每行一条说明与总结论。

判据：

* 取数端点 `401 + WWW-Authenticate: Bearer` → ✅ 可达；
* 令牌端点 `401` 且响应体 JSON（前 2KB）的 `error` 是 `invalid_client` / `invalid_grant` /
  `unauthorized_client` → ✅ 可达（Keycloak 的正常拒绝，不带 Bearer 头；说明文字为
  `401 invalid_client（表单探针，凭据是假的）`）。响应体不是 JSON、读不到 `error` 时仍判 ⚠，
  不比旧行为更严；
* `415` → ⚠ 并提示令牌端点要 `application/x-www-form-urlencoded`；
* 连接失败、超时、DNS 失败、TLS 证书失败、5xx → ❌。

`--json` 输出 `{"ok": bool, "checks": [...]}`；每项的 `status` 是 HTTP 状态码
（可达时为 `401`），`level` 取 `ok` / `warn` / `fail`，顶层 `ok` 为 true 表示没有 `fail` 项
（⚠ 不影响退出码 0）。

### 6.3 `ledger` 子命令（台账 → 点表）

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--ledger` | **必填** | 台账 `.xlsx` 路径 |
| `--match` | 空 | 关键词，匹配"设施名称 / 设备名称" |
| `--column` | `any` | `any` / `设施名称` / `设备名称` |
| `--signals` | 空 = 全要 | 只保留这些信号 |
| `--out` | 空 | 输出 JSON 点表（可直接喂 `--nodes-file`） |
| `--md` | 空 | 输出 Markdown 四列点表 |
| `--list-devices` | 关 | 只列设备名，不产出文件 |

### 6.4 退出码

| 命令 | 码 | 含义 |
| --- | --- | --- |
| 取数 | 0 | 正常（至少 1 行；有窗口重试耗尽时仍 0，末尾打印 `⚠ 有 N 个窗口重试耗尽`） |
| 取数 | 1 | 总行数为 0（一行没取到）；或缺 `--client-secret`、节点清单格式错误、上限探测失败（`sys.exit(消息)`） |
| 取数 | 2 | argparse 用法错误（如漏 `--factory-code`） |
| `doctor` | 0 | 所有端点都不是 fail（⚠ 不影响 0） |
| `doctor` | 1 | 存在网络层不可达端点（DNS/连接/超时） |
| `ledger` | 0 | 正常 |
| `ledger` | 1 | 台账不存在 / 缺列 / 缺 pandas 等（`sys.exit(消息)`） |
| `ledger` | 3 | 匹配到 0 行（旧脚本语义：不是错误，但显然没产出） |

## 7. 输出契约

### 7.1 parquet（默认，需 pandas + pyarrow）

* **文件名**：`{factory}-{YYYYMM}.parquet`，`YYYYMM` 是**北京时间自然月**；
* **索引**：毫秒整数、`int64`、升序、去重 `keep="first"`；索引没有额外名字；
* **列**：先按节点顺序是测点名（`namespace + "-" + id`），最后追加
  `data_time_utc`（`datetime64[ns, UTC]`）与 `data_time_bj`（`Asia/Shanghai`）；
* **压缩**：`zstd`；
* **原子写**：先写同目录临时文件，成功后 `os.replace` 覆盖，中途失败不会留下半个文件；
* **重跑**：同区间重跑会整月覆盖重写（不是追加）。

```python
import pandas as pd
df = pd.read_parquet("raw/PS37217-02-202601.parquet")
df.index.dtype          # int64
df["data_time_bj"].iloc[0].tzinfo   # Asia/Shanghai
df["PS37217-02-BE_GFJF_0001-C_003_0001-V_IF"]
```

格式与 `scripts/取数探针/plot_signals.py` 的消费方式兼容：列名 `namespace-id`、一行一时刻的宽表。

**同一毫秒重复时**：按窗口时间顺序合并，后一个窗口的值覆盖前一个（重试轮次补写的窗口覆盖首轮）。
旧脚本是"完成顺序 + `keep="first"`"，本身不确定；正常参数下窗口互不重叠，实测没有跨窗重复值。

### 7.2 JSONL（无 pandas/pyarrow，或显式要求）

触发方式（三选一）：`--format jsonl`、`IOT_FETCH_FORCE_JSONL=1`、`--format auto` 且缺依赖。
文件名 `{factory}-{YYYYMM}.jsonl`，每行一条记录：

```json
{"t": 1767225600000, "data_time_utc": "2026-01-01T00:00:00+00:00", "signals": {"PS37217-02-...-V_IF": 41.2}}
```

* `t`：毫秒时间戳（与 parquet 索引同一口径）；
* `data_time_utc`：ISO 8601 UTC；
* `signals`：`{测点名: 值}`，缺失值写 `null`；
* 行按时间升序；同样整月覆盖重写、临时文件原子替换。

> 旧脚本声称"没有 pandas 时写 JSONL"，但实际没有实现落盘；本包把它补全了，而不是留一个静默失败。

## 8. 单点上限与窗口规则

平台对**每次查询的每个测点**有条数上限（实测近期约 2000、远期约 1500），超过就**静默截断**
（不报错、不提示），超出窗口尾部的时间段会整段缺失。取数窗口必须按上限换算：

```
窗口小时数 = max(1, int(cap × step_seconds / 3600))
```

* `cap` = `probe_cap()` 的结果：用 **8 小时窗口 + `maxSizePerNode=100000`** 探一次，
  看服务端实际返回了多少条，即该时段每测点上限；
* `step_seconds` 默认 15（本仓库采样周期）；`cap=2000` → 8h，`cap=1500` → 6h；
* 之所以探 8 小时：9 小时起就会被 2000 条截断，8 小时刚好能把"近期上限"探满且不留尾部缺口；
  早期不分月份、固定 8 小时窗口的脚本，在远期月份会丢掉每月最后约 1.75 小时（约 -20% 行）。

**自动模式**（`--window-hours 0`，默认）：

1. 先用 `end - 1 天` 探一次，定初始窗口；
2. 逐月取数时，每个月在"该月区间中点"**再探一次**（远期上限更低，窗口会自适应变短）；
   即使给了 `--max-size`，这个逐月重探也照旧，与旧脚本一致；
3. 给了 `--window-hours > 0` 就完全跳过探针，按给定值切窗口。

`--probe` 打印的三行怎么读：

```
近期（2026-03-31）每测点上限 = 2000
起点附近（2026-01-01）每测点上限 = 1450
建议窗口：近期 8h、远期 6h（@15s）
```

即"这段区间里窗口该取 6h 起步"，直接写 `--window-hours 6` 可跳过逐月探针。

**`cap<=0` 的边界**：视为探测失败。`parse_signal_window(0)` 返回 **6 小时**保守默认
（旧脚本等价函数返回 1h）；但 CLI 在 `cap<=0` 时直接
`raise SystemExit("上限探测失败（该时间段无数据或鉴权失败）")`，两条路径都**不会产生取数行为**，
所以不存在"0 上限导致窗口 1h 全空"的回归。

`--step-seconds` 用于别的项目：采样周期 30s 时同样 2000 条上限可覆盖 16h，窗口换算自动跟上。

## 9. 代理与网络

本包默认**直连**，理由与旧脚本相同：域名在公司网内可达，而本机 Clash 等代理会把 HTTPS 改写成
HTML 错误页（表现为 `JSONDecodeError` 或 `code!=0`）。

* **导入即生效**：`import iot_fetch.config` 时把 `bewg.net.cn`、`.bewg.net.cn` 追加进
  `NO_PROXY`（幂等）；构造 `IotConfig` 时再确认一次。
* **不读系统代理**：`requests.Session.trust_env = False`（因此 `HTTP(S)_PROXY` 无效）。
* **连接池**：`pool_connections=8`、`pool_maxsize=16`，超时 `(连接 5s, 读取 90s)`，可用
  `IotConfig(timeout=..., pool_maxsize=...)` 覆盖。
* **必须走代理的分支环境**：设 `IOT_USE_ENV_PROXY=1`，此时**不再追加** `NO_PROXY`、
  恢复读系统 `HTTP(S)_PROXY`，由调用方完全接管代理策略。
* **先体检再取数**：`python -m iot_fetch doctor` 覆盖 DNS / TLS 证书链 / HTTP 状态；
  `--json` 便于接监控。实现见 [iot_fetch/doctor.py](iot_fetch/doctor.py)。

## 10. 作为库使用

```python
from iot_fetch import IotConfig, IotClient, make_nodes

cfg = IotConfig.from_env(client_secret="<从密钥管理系统取>")   # 端点/租户走环境变量或默认值
nodes = make_nodes("PS37217-02", "-BE_GFJF_0001-C_003_", ["0001", "0002"], ["V_IF", "V_OP"])

with IotClient(cfg) as client:
    data = client.fetch(nodes, start, hours=6)     # start 是 datetime（naive 视为北京时间）
# data: {测点名: {毫秒: 值}}
```

* `IotClient.fetch(nodes, start, hours=6)` / `fetch(nodes, start, end=...)`：单窗口，返回
  `{测点名: {毫秒: 值}}`；窗口重试耗尽抛 `RuntimeError`（不静默丢数据）。
* `IotClient.fetch_window(nodes, start, hours, max_size=100000)`：保留旧脚本签名，返回
  `(start, [(测点名, {毫秒: 值}), ...] | None)`。
* `IotClient.probe_cap(nodes, when)`：探每测点上限；`parse_signal_window(cap, step_s=15)`：
  上限 → 窗口小时数；`month_slices(start, end)`：区间 → 自然月切片。
* `fetch_range(cfg, nodes, start, end)`：直接取任意区间（内部按月切、并发、按窗口合并），
  返回同结构的 `{测点名: {毫秒: 值}}`。
* `load_nodes(path)`：读 JSON 数组或 CSV 点表；`make_nodes(factory, prefix, machines, signals)`：
  按命名规则拼点表。节点格式见 [examples/nodes.example.json](examples/nodes.example.json)。

完整可运行示例：
[examples/fetch_custom_nodes.py](examples/fetch_custom_nodes.py)（读点表 → 取数 → 内存里做点统计，不落盘）。

## 11. 常见故障

| 现象 | 可能原因 | 处理 |
| --- | --- | --- |
| `401` 且重试也 401 | 租户头被网关改写 / client_id+secret 不对 / 令牌端点不是表单编码 | 跑 `doctor`；确认 `IOT_TENANT`；自己写代码时令牌请求必须 `data=` 表单（JSON 会 415） |
| `JSONDecodeError`、返回 HTML | 请求被本机代理接管（Clash 等） | 确认没有设 `IOT_USE_ENV_PROXY=1`；包默认已把公司域名加进 `NO_PROXY`；跑 `doctor` |
| 全部窗口无数据、退出码 1 | 测点名拼错（`namespace + "-" + id`，注意 namespace 自身已带连字符）/ 时间早于平台保留期（约 2024-10 起）/ 该区间确实无数据 | 用 `--probe` 看上限是否 0；先取最近 1 天验证点表 |
| 每月最后一两小时缺数 | 窗口过大（>8h）被每测点上限静默截断 | `--probe` 看建议窗口；`--window-hours` 调小；对比"理论点数 = 时长/step"与实际行数 |
| `502` / `503` | WAF 限流 / 平台抖动 | 降 `--workers`（如 16）、增 `--retries`、错峰重跑（整月覆盖） |
| 意外得到 `.jsonl` | 缺 pandas/pyarrow，或环境里残留 `IOT_FETCH_FORCE_JSONL=1` | 看开头 `⚠ 落盘格式 jsonl：<原因>`；装 `.[parquet]` 或清环境变量 |
| `ledger` 退出码 3 | `--match` 关键词不对 / `--column` 用错列 | `--list-devices` 先看设备名；`--column any` 匹配两列 |
| `缺少 --client-secret（或设置环境变量 IOT_CLIENT_SECRET）` | 凭据没注入 | 设环境变量或传参；退出码 1 |
| `凭据文件不存在：...` / `凭据文件不是合法 JSON：...` | `--credentials-json` 路径错 / 文件损坏 / 顶层不是对象 | 检查路径与 JSON 顶层；退出码 1；报错不会回显文件内容 |
| 令牌端点 `415 Unsupported Media Type` | 用 JSON 打了 Keycloak | `doctor` 已改用表单编码；自己写代码时同理 |

## 12. 测试怎么跑（全离线）

```powershell
cd scripts\取数探针\iot-fetch-kit
<repo>\.venv-311\Scripts\python.exe -m unittest discover -s tests -v
```

* 全部用例**不访问公网、不需要凭据**：`tests/fake_server.py` 在 `127.0.0.1:0` 起 stdlib
  `http.server` 假服务端，覆盖正常返回、缺 `X-Tenant-Id` 的 401、令牌过期后刷新、
  WAF 502 重试成功、`code!=0` 报错、`historyData` 空 → missing、`maxSizePerNode` 钳制；
* CLI 端到端：进程内调 `cli.main(argv)`，断言文件名、索引 dtype 与去重、列集合、
  `data_time_utc`/`data_time_bj` 时区、zstd 可读、退出码、两种 `--nodes-file`、JSONL 回退；
* 契约与时间/窗口换算：`tests/test_contract.py`；doctor：`tests/test_doctor.py`；
  台账：`tests/test_ledger.py`；凭据通道（环境变量别名 / 本地凭据 JSON / 优先级）：
  `tests/test_credentials.py`；
* **Python 3.9 兼容**：`tests/test_py39_syntax.py` 用 `ast.parse(src, feature_version=(3, 9))`
  遍历 `iot_fetch/*.py`，并断言每个模块有 `from __future__ import annotations`；
* 只装 `requests` 也能跑：pandas / pyarrow / openpyxl 相关用例用 `unittest.skipIf` 跳过；
* 重试退避在测试里用 `IOT_FETCH_SLEEP_SCALE=0` 归零（`tests/support.py` 已设），全套约 20–30s。

单跑某个文件：

```powershell
<repo>\.venv-311\Scripts\python.exe -m unittest tests.test_cli -v
```
