"""Append-only, hash-chained audit history."""
from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, List, Optional


def to_plain(obj: Any) -> Any:
    """Convert dataclasses / dates into JSON-friendly structures."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return to_plain(dataclasses.asdict(obj))
    if isinstance(obj, dict):
        return {str(k): to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_plain(v) for v in obj]
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    return obj


@dataclass(frozen=True)
class AuditEntry:
    seq: int
    timestamp: str
    actor: str
    action: str
    entity_type: str
    entity_id: str
    before: Any
    after: Any
    prev_hash: str
    hash: str


class AuditLog:
    GENESIS = "0" * 64

    def __init__(self, now=datetime.utcnow):
        self._entries: List[AuditEntry] = []
        self._now = now

    @staticmethod
    def _digest(payload: dict) -> str:
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()

    def record(self, actor, action, entity_type, entity_id, before=None, after=None) -> AuditEntry:
        prev = self._entries[-1].hash if self._entries else self.GENESIS
        payload = dict(seq=len(self._entries) + 1, timestamp=self._now().isoformat(), actor=actor,
                       action=action, entity_type=entity_type, entity_id=str(entity_id),
                       before=to_plain(before), after=to_plain(after), prev_hash=prev)
        entry = AuditEntry(hash=self._digest(payload), **payload)
        self._entries.append(entry)
        return entry

    def history(self, entity_type: Optional[str] = None, entity_id: Optional[str] = None,
                action: Optional[str] = None) -> List[AuditEntry]:
        return [e for e in self._entries
                if (entity_type is None or e.entity_type == entity_type)
                and (entity_id is None or e.entity_id == str(entity_id))
                and (action is None or e.action == action)]

    def verify(self) -> bool:
        """Recompute the hash chain; False if any entry was altered or removed."""
        prev = self.GENESIS
        for e in self._entries:
            payload = dict(seq=e.seq, timestamp=e.timestamp, actor=e.actor, action=e.action,
                           entity_type=e.entity_type, entity_id=e.entity_id, before=e.before,
                           after=e.after, prev_hash=e.prev_hash)
            if e.prev_hash != prev or self._digest(payload) != e.hash:
                return False
            prev = e.hash
        return True

    def __len__(self):
        return len(self._entries)
