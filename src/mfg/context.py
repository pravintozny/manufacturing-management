"""Shared in-memory state, id generation, clock and audit hook."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from typing import Callable, Dict

from .audit import AuditLog, to_plain
from .errors import NotFoundError

EPS = 1e-9


def q(x: float) -> float:
    """Round quantities/money to 6 places to avoid float noise."""
    return round(x + 0.0, 6)


class Context:
    def __init__(self, today: Callable[[], date] = date.today, actor: str = "system"):
        self._today = today
        self.actor = actor
        self.audit = AuditLog()
        self.categories: Dict[str, object] = {}
        self.products: Dict[str, object] = {}
        self.suppliers: Dict[str, object] = {}
        self.materials: Dict[str, object] = {}
        self.boms: Dict[str, list] = {}
        self.work_centers: Dict[str, object] = {}
        self.orders: Dict[str, object] = {}
        self.defects: Dict[str, object] = {}
        self.inspections: Dict[str, object] = {}
        self._counters: Dict[str, int] = {}

    def today(self) -> date:
        return self._today()

    def next_id(self, prefix: str) -> str:
        n = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = n
        return f"{prefix}-{n:05d}"

    @contextmanager
    def as_user(self, actor: str):
        prev, self.actor = self.actor, actor
        try:
            yield
        finally:
            self.actor = prev

    def log(self, action, entity_type, entity_id, before=None, after=None):
        self.audit.record(self.actor, action, entity_type, entity_id,
                          to_plain(before), to_plain(after))

    # lookups -------------------------------------------------------------
    def get(self, store: Dict, key, label: str):
        try:
            return store[key]
        except KeyError:
            raise NotFoundError(f"{label} '{key}' not found") from None
