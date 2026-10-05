"""只追加事件仓库。

核心约束：
- 事件只能 append，不提供更新/删除接口；复检、申诉、迟到订单等均以
  后继事件表达，原始证据的标识、时间与载荷永不被改写。
- ``event_id`` 是仓库事件标识，跨部门重复上报同一条记录时幂等去重；
  部门本地编号（source_record_id）不参与判定。
- 同一聚合的事件构成严格递增版本流（1,2,3…），用于乐观并发控制。
- 持久化为 JSONL，每行一条完整事件；进程重启后重放恢复。
"""

import json
import os
import threading
from pathlib import Path

from .errors import ConflictError, ValidationError
from .validator import validate_event


class EventStore:
    def __init__(self, path: str | os.PathLike[str] | None = None):
        self._path = Path(path) if path else None
        self._lock = threading.RLock()
        self._events: list[dict] = []
        self._by_id: dict[str, dict] = {}
        self._versions: dict[tuple[str, str], int] = {}
        self._seq = 0
        if self._path and self._path.exists():
            with self._path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        self._ingest(json.loads(line), persist=False)
            self._seq = len(self._events)

    # ---------------------------------------------------------- 写入

    def append(self, record: dict) -> dict:
        """写入一条事件。

        返回仓库中保存的事件副本。若 ``event_id`` 已存在：
        - 内容一致：视为重复上报，原样返回已存事件（幂等成功）；
        - 内容不一致：抛 ConflictError，防止“同编号改证据”。
        """
        errors = validate_event(record)
        if errors:
            raise ValidationError(errors)

        with self._lock:
            existing = self._by_id.get(record["event_id"])
            if existing is not None:
                if self._canonical(existing) == self._canonical(record):
                    return dict(existing)
                raise ConflictError(
                    f"event_id={record['event_id']} 已存在且内容不同；"
                    "初始证据不可改写，业务更正请另发后继事件")

            key = (record["aggregate_type"], record["aggregate_id"])
            expected = self._versions.get(key, 0) + 1
            if record["version"] != expected:
                raise ConflictError(
                    f"聚合 {record['aggregate_type']}:{record['aggregate_id']} "
                    f"下一个版本应为 {expected}，收到 {record['version']}")

            stored = dict(record)
            stored["seq"] = self._seq
            self._seq += 1
            self._ingest(stored, persist=True)
            return dict(stored)

    def _ingest(self, stored: dict, *, persist: bool) -> None:
        self._events.append(stored)
        self._by_id[stored["event_id"]] = stored
        key = (stored["aggregate_type"], stored["aggregate_id"])
        self._versions[key] = stored["version"]
        if persist and self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(stored, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())

    @staticmethod
    def _canonical(record: dict) -> str:
        payload = {k: v for k, v in record.items() if k != "seq"}
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    # ---------------------------------------------------------- 读取

    def get(self, event_id: str) -> dict | None:
        found = self._by_id.get(event_id)
        return dict(found) if found else None

    def stream(self, aggregate_type: str | None = None,
               aggregate_id: str | None = None) -> list[dict]:
        """按写入顺序读取事件，可按聚合流过滤。"""
        with self._lock:
            out = self._events
            if aggregate_type is not None:
                out = [e for e in out if e["aggregate_type"] == aggregate_type]
            if aggregate_id is not None:
                out = [e for e in out if e["aggregate_id"] == aggregate_id]
            return [dict(e) for e in out]

    def next_version(self, aggregate_type: str, aggregate_id: str) -> int:
        return self._versions.get((aggregate_type, aggregate_id), 0) + 1

    def __len__(self) -> int:
        return len(self._events)
