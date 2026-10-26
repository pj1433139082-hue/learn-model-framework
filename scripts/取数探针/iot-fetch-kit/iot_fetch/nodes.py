"""测点清单：由「厂站码 + 机台 + 信号」拼，或从 JSON / CSV 读。

旧脚本支持 `--nodes-file`（JSON 数组 `[{"namespace": ..., "id": ...}]`）；
本包沿用并**新增 CSV**（表头 `namespace,id`），方便用 Excel 维护点表。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

__all__ = ["make_nodes", "load_nodes", "save_nodes", "node_name", "validate_nodes"]


def node_name(node: Dict[str, Any]) -> str:
    """测点名 = `namespace + "-" + id`（parquet 列名、missing 统计的键）。"""
    return "{0}-{1}".format(node["namespace"], node["id"])


def make_nodes(
    factory: str,
    prefix: str,
    machines: Sequence[str],
    signals: Sequence[str],
) -> List[Dict[str, str]]:
    """按「机台 × 信号」拼节点清单（旧脚本 `build_nodes` 的等价实现）。

    命名空间 = `factory + prefix + machine`，例如
    `PS37217-02` + `-BE_GFJF_0001-C_003_` + `0001`。

    ```
    make_nodes("PS37217-02", "-BE_GFJF_0001-C_003_", ["0001"], ["V_IF"])
    # [{"id": "V_IF", "namespace": "PS37217-02-BE_GFJF_0001-C_003_0001"}]
    ```
    """
    return [
        {"id": str(signal), "namespace": "{0}{1}{2}".format(factory, prefix, machine)}
        for machine in machines
        for signal in signals
    ]


def _clean(value: Any) -> str:
    return str(value if value is not None else "").strip()


def validate_nodes(raw: Any, *, source: str = "nodes") -> List[Dict[str, str]]:
    """校验节点清单形状，返回只含 `namespace`/`id` 的干净列表。"""
    if not isinstance(raw, list):
        raise ValueError(
            "{0}：需要 JSON 数组 [{{\"namespace\": ..., \"id\": ...}}]，实际是 {1}".format(
                source, type(raw).__name__
            )
        )
    cleaned: List[Dict[str, str]] = []
    seen = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError("{0}：第 {1} 项不是对象".format(source, index))
        namespace = _clean(item.get("namespace"))
        ident = _clean(item.get("id"))
        if not namespace or not ident:
            raise ValueError(
                "{0}：第 {1} 项缺少 namespace 或 id（少尾缀时接口会静默返回 null）".format(
                    source, index
                )
            )
        key = (namespace, ident)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append({"namespace": namespace, "id": ident})
    if not cleaned:
        raise ValueError("{0}：节点清单为空".format(source))
    return cleaned


def _load_json(path: Path) -> List[Dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig")
    data = json.loads(text)
    if isinstance(data, dict) and isinstance(data.get("nodes"), list):
        data = data["nodes"]
    return validate_nodes(data, source=str(path))


def _load_csv(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("{0}：CSV 为空（需要表头 namespace,id）".format(path))
        columns = {(name or "").strip().lower(): name for name in reader.fieldnames}
        if "namespace" not in columns or "id" not in columns:
            raise ValueError(
                "{0}：CSV 表头必须是 namespace,id（实际：{1}）".format(
                    path, reader.fieldnames
                )
            )
        rows: List[Dict[str, Any]] = []
        for row in reader:
            namespace = _clean(row.get(columns["namespace"]))
            ident = _clean(row.get(columns["id"]))
            if not namespace and not ident:
                continue
            rows.append({"namespace": namespace, "id": ident})
    return validate_nodes(rows, source=str(path))


def load_nodes(path: Any) -> List[Dict[str, Any]]:
    """读节点清单：`.json`（数组，或 `{"nodes": [...]}`）或 `.csv`（表头 namespace,id）。

    ```
    nodes = load_nodes("nodes.json")   # [{"namespace": "...", "id": "V_IF"}, ...]
    ```
    """
    target = Path(path)
    if not target.is_file():
        raise ValueError("节点清单不存在：{0}".format(target))
    suffix = target.suffix.lower()
    if suffix == ".csv":
        return _load_csv(target)
    if suffix in (".json", ".jsonc", ""):
        return _load_json(target)
    raise ValueError(
        "节点清单只支持 .json / .csv：{0}".format(target)
    )


def save_nodes(nodes: Iterable[Dict[str, Any]], path: Any) -> Path:
    """把节点清单写成 JSON 数组（缩进 2，中文不转义），返回路径。"""
    target = Path(path)
    if target.parent and str(target.parent) not in ("", "."):
        target.parent.mkdir(parents=True, exist_ok=True)
    payload = [{"namespace": node["namespace"], "id": node["id"]} for node in nodes]
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target
