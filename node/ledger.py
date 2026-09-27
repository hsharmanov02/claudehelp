"""Append-only ledger of everything the node does and spends.

One JSON object per line, one file per month (`2026-09.jsonl`), so files stay
small, diff cleanly in git and never need rewriting.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Ledger:
    directory: Path

    def __post_init__(self) -> None:
        self.directory = Path(self.directory)

    def _file_for(self, when: datetime) -> Path:
        return self.directory / f"{when:%Y-%m}.jsonl"

    def append(self, kind: str, when: datetime | None = None, **fields: Any) -> dict:
        when = when or utc_now()
        entry = {"ts": when.isoformat(), "kind": kind, **fields}
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._file_for(when)
        line = json.dumps(entry, sort_keys=True, default=str)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        return entry

    def entries(self, month: datetime | None = None) -> Iterator[dict]:
        """Entries for one month, or for every month when `month` is None."""
        if month is not None:
            paths = [self._file_for(month)]
        else:
            paths = sorted(self.directory.glob("*.jsonl"))
        for path in paths:
            if not path.exists():
                continue
            with path.open(encoding="utf-8") as f:
                for line_number, line in enumerate(f, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        # A torn final line from a killed run must not stop the
                        # node from reading everything else.
                        continue

    def spend_for_month(self, month: datetime | None = None) -> float:
        month = month or utc_now()
        return sum(
            float(e.get("cost_usd", 0.0))
            for e in self.entries(month)
            if e.get("kind") == "spend"
        )
