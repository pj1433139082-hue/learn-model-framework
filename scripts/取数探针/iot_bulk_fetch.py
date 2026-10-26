"""IoT 历史数据批量取数入口（直连 REST，不依赖 3.11 密文 SDK）

真正实现在可移植包 `scripts/取数探针/iot-fetch-kit`（`iot_fetch` 包），本文件只是仓库内的
调用入口：参数、默认值、退出码与落盘口径都由该包提供，把取数能力搬到别的项目时整包拷
`iot-fetch-kit` 即可，移植与参数说明见该包自带的 README.md。

仓库内默认凭据：未显式传 --credentials-json / --client-secret，也没设 IOT_CREDENTIALS_JSON 时，
自动读取同目录的 iot-credentials.local.json（存在才用）；显式参数和环境变量仍然优先。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_INSTALL_HINT = (
    "找不到 iot_fetch 取数包（可移植包 iot-fetch-kit）。两种修法：\n"
    "  1) 在仓库根安装一次：pip install -e scripts\\取数探针\\iot-fetch-kit\n"
    "  2) 或者把 scripts\\取数探针\\iot-fetch-kit 整个目录拷到本脚本旁边（保持同名），\n"
    "     本脚本会自动把它加入 sys.path。\n"
    "参数与移植说明见 iot-fetch-kit\\README.md。"
)


_CREDENTIALS_FILENAME = "iot-credentials.local.json"


def _local_credentials_path():
    """同目录有本地凭据文件就返回它（仓库内入口的默认凭据来源）。"""
    candidate = Path(__file__).resolve().parent / _CREDENTIALS_FILENAME
    return candidate if candidate.is_file() else None


def _use_local_credentials():
    """未显式指定凭据时，把同目录的本地凭据文件设成 IOT_CREDENTIALS_JSON。"""
    if os.environ.get("IOT_CREDENTIALS_JSON"):
        return
    path = _local_credentials_path()
    if path is not None:
        os.environ["IOT_CREDENTIALS_JSON"] = str(path)


def _load_cli():
    """先按已安装场景找 iot_fetch，找不到再退回同目录下的可移植包。"""
    try:
        from iot_fetch import cli
    except ImportError:
        pass
    else:
        return cli

    kit = Path(__file__).resolve().parent / "iot-fetch-kit"
    if kit.is_dir():
        sys.path.insert(0, str(kit))
    try:
        from iot_fetch import cli
    except ImportError as exc:
        raise SystemExit(f"错误：{exc}\n{_INSTALL_HINT}") from exc
    return cli


if __name__ == "__main__":
    cli = _load_cli()
    _use_local_credentials()
    raise SystemExit(cli.main())
