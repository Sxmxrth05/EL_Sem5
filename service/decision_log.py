"""Append-only JSON-lines decision log (explainability and audit trail)."""

from __future__ import annotations

import json
import threading
from collections import deque
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path


class DecisionLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    @staticmethod
    def entry(inputs: dict, result: dict) -> dict:
        return {
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "inputs": inputs,
            "score": result["score"],
            "raw_total": result["raw_total"],
            "breakdown": result["breakdown"],
            "tier": result["tier"],
            "thresholds": result["thresholds"],
            "policy_version": result["policy_version"],
        }

    def append_many(self, entries: Iterable[dict]) -> None:
        text = "".join(json.dumps(e, separators=(",", ":")) + "\n" for e in entries)
        if not text:
            return
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(text)

    def append(self, entry: dict) -> None:
        self.append_many([entry])

    def recent(self, limit: int) -> list[dict]:
        """Most recent entries first. Corrupt lines are skipped, not fatal."""
        if not self.path.exists():
            return []
        with self._lock, self.path.open(encoding="utf-8") as f:
            tail = deque(f, maxlen=limit)
        out = []
        for line in reversed(tail):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
