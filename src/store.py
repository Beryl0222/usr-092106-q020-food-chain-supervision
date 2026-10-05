"""不可变事件仓库。

- 只追加：任何已接收事件不提供改写入口；业务更正只能追加后继事件。
- 跨部门去重：同一 event_id 重复报送，内容一致则幂等忽略，内容不一致则拒绝，
  防止不同部门重复建账或借重发改写证据。
- 聚合版本：同一 aggregate_id 内 version 从 1 起单调递增。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from .validator import validate_event

# 参与内容指纹的字段（recorded_at 是接收时间，不参与）
_FINGERPRINT_KEYS = (
    "event_type",
    "aggregate_type",
    "aggregate_id",
    "occurred_at",
    "version",
    "source_department",
    "payload",
    "summary",
)


class EventError(ValueError):
    """事件违反仓库约束（结构非法、版本冲突、标识冲突）。"""


@dataclass(frozen=True)
class AppendResult:
    event_id: str
    accepted: bool          # False 表示命中幂等去重
    deduped: bool


def canonical_fingerprint(event: dict) -> str:
    material = {key: event.get(key) for key in _FINGERPRINT_KEYS}
    return json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class EventStore:
    def __init__(self) -> None:
        self._events: list[dict] = []
        self._index: dict[str, int] = {}                 # event_id -> 下标
        self._fingerprints: dict[str, str] = {}
        self._agg_versions: dict[tuple[str, str], int] = {}

    # —— 写入 ——

    def append(self, event: dict) -> AppendResult:
        errors = validate_event(event)
        if errors:
            raise EventError("；".join(errors))

        event_id = event["event_id"]
        fingerprint = canonical_fingerprint(event)
        existing_pos = self._index.get(event_id)
        if existing_pos is not None:
            if self._fingerprints[event_id] != fingerprint:
                raise EventError(
                    f"事件标识 {event_id} 已存在但内容不一致；禁止改写，"
                    "更正请追加后继事件"
                )
            return AppendResult(event_id=event_id, accepted=False, deduped=True)

        agg_key = (event["aggregate_type"], event["aggregate_id"])
        expected = self._agg_versions.get(agg_key, 0) + 1
        if event["version"] != expected:
            raise EventError(
                f"聚合 {agg_key} 下一版本应为 {expected}，收到 {event['version']}"
            )

        self._index[event_id] = len(self._events)
        self._fingerprints[event_id] = fingerprint
        self._agg_versions[agg_key] = expected
        self._events.append(event)
        return AppendResult(event_id=event_id, accepted=True, deduped=False)

    def append_many(self, events: Iterable[dict]) -> list[AppendResult]:
        return [self.append(event) for event in events]

    # —— 读取 ——

    def stream(self) -> Iterator[dict]:
        yield from self._events

    def get(self, event_id: str) -> dict | None:
        pos = self._index.get(event_id)
        return self._events[pos].copy() if pos is not None else None

    def aggregate_stream(self, aggregate_type: str, aggregate_id: str) -> list[dict]:
        return [
            event
            for event in self._events
            if event["aggregate_type"] == aggregate_type
            and event["aggregate_id"] == aggregate_id
        ]

    def by_type(self, event_type: str) -> list[dict]:
        return [event for event in self._events if event["event_type"] == event_type]

    def version_of(self, aggregate_type: str, aggregate_id: str) -> int:
        return self._agg_versions.get((aggregate_type, aggregate_id), 0)

    def __len__(self) -> int:
        return len(self._events)

    # —— 装载/落盘（JSONL，顺序即接收顺序） ——

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "EventStore":
        store = cls()
        with Path(path).open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    store.append(json.loads(line))
        return store

    def write_jsonl(self, path: str | Path) -> None:
        with Path(path).open("w", encoding="utf-8") as handle:
            for event in self._events:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
