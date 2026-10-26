<#
.SYNOPSIS
    示例：用 iot-fetch 给一个厂站批量取历史数据（PowerShell）。

.DESCRIPTION
    演示常见参数组合：指定厂站码与机台、按自然月把一段时间取到 raw/ 目录。
    凭据只从环境变量读（IOT_CLIENT_SECRET），不要写进脚本、不要提交到仓库。

.EXAMPLE
    # 先设好凭据（只在当前会话生效）
    $env:IOT_CLIENT_SECRET = "<redacted>"
    # 取 2026 年 1—3 月
    ./examples/fetch_factory.ps1 -FactoryCode PS37217-02 -MachinePrefix "-BE_GFJF_0001-C_003_" -Machine 0001,0002 -Start 2026-01-01 -End "2026-04-01 00:00"

.NOTES
    - --machine-prefix 以 '-' 开头时，PowerShell 会把值当成参数名，必须写成 --machine-prefix=值（脚本里已处理）。
    - 完整参数见 README 第 6 章，或 python -m iot_fetch --help。
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $FactoryCode,

    [string] $MachinePrefix = "",

    [string[]] $Machine = @(),

    [string] $Signals = "V_IF,V_OP,V_P,V_ITIT,V_I,S_RUN",

    [string] $Start = "",

    [string] $End = "",

    [string] $Out = "raw",

    [int] $Workers = 32,

    [double] $WindowHours = 0,

    [int] $MaxSize = 0,

    [int] $Retries = 4,

    # 解释器路径；留空表示用 PATH 里的 python
    [string] $Python = ""
)

$ErrorActionPreference = "Stop"

if (-not $env:IOT_CLIENT_SECRET) {
    Write-Error "缺少环境变量 IOT_CLIENT_SECRET（凭据不要写进脚本）"
    exit 1
}

$py = if ($Python) { $Python } else { "python" }

$argv = @(
    "-m", "iot_fetch",
    "--factory-code", $FactoryCode,
    "--signals", $Signals,
    "--out", $Out,
    "--workers", "$Workers",
    "--window-hours", "$WindowHours",
    "--max-size", "$MaxSize",
    "--retries", "$Retries"
)

# 值以 '-' 开头时必须用 '=' 形式，否则 PowerShell 会把它当参数名。
if ($MachinePrefix) { $argv += "--machine-prefix=$MachinePrefix" }
if ($Machine.Count -gt 0) { $argv += @("--machine", ($Machine -join ",")) }
if ($Start) { $argv += @("--start", $Start) }
if ($End) { $argv += @("--end", $End) }

Write-Host "执行：$py $($argv -join ' ')"
& $py @argv
exit $LASTEXITCODE
