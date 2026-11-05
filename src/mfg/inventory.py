"""Raw material inventory, suppliers, and shared stock-movement primitives."""
from __future__ import annotations

import copy
from typing import List, Optional, Tuple

from .context import EPS, Context, q
from .errors import DuplicateError, InsufficientStockError, ValidationError
from .models import Material, Supplier


def _nonneg(value: float, label: str):
    if value < 0:
        raise ValidationError(f"{label} must be >= 0")


class InventoryService:
    def __init__(self, ctx: Context):
        self.ctx = ctx

    # suppliers -------------------------------------------------------------
    def add_supplier(self, id: str, name: str, contact: str = "", lead_time_days: int = 0) -> Supplier:
        if id in self.ctx.suppliers:
            raise DuplicateError(f"supplier '{id}' exists")
        if lead_time_days < 0:
            raise ValidationError("lead time must be >= 0")
        s = Supplier(id, name, contact, lead_time_days)
        self.ctx.suppliers[id] = s
        self.ctx.log("create", "supplier", id, after=s)
        return s

    # materials -------------------------------------------------------------
    def create_material(self, code: str, name: str, unit: str, supplier_id: Optional[str],
                        unit_cost: float, reorder_level: float = 0.0, reorder_qty: float = 0.0,
                        on_hand: float = 0.0) -> Material:
        code = code.strip().upper()
        if not code:
            raise ValidationError("material code required")
        if code in self.ctx.materials:
            raise DuplicateError(f"material '{code}' exists")
        if supplier_id is not None:
            self.ctx.get(self.ctx.suppliers, supplier_id, "supplier")
        for v, l in ((unit_cost, "unit cost"), (reorder_level, "reorder level"),
                     (reorder_qty, "reorder qty"), (on_hand, "on hand")):
            _nonneg(v, l)
        m = Material(code, name, unit, supplier_id, unit_cost, on_hand, 0.0, reorder_level, reorder_qty)
        self.ctx.materials[code] = m
        self.ctx.log("create", "material", code, after=m)
        return m

    def get(self, code: str) -> Material:
        return self.ctx.get(self.ctx.materials, code.upper(), "material")

    def update_material(self, code: str, *, supplier_id=None, unit_cost=None,
                        reorder_level=None, reorder_qty=None) -> Material:
        m = self.get(code)
        before = copy.deepcopy(m)
        if supplier_id is not None:
            self.ctx.get(self.ctx.suppliers, supplier_id, "supplier")
            m.supplier_id = supplier_id
        for attr, v in (("unit_cost", unit_cost), ("reorder_level", reorder_level),
                        ("reorder_qty", reorder_qty)):
            if v is not None:
                _nonneg(v, attr)
                setattr(m, attr, v)
        self.ctx.log("update", "material", m.code, before, m)
        return m

    def receive(self, code: str, qty: float, unit_cost: Optional[float] = None) -> Material:
        """Goods receipt; unit cost becomes the weighted average."""
        m = self.get(code)
        if qty <= 0:
            raise ValidationError("receipt qty must be > 0")
        if unit_cost is not None:
            _nonneg(unit_cost, "unit cost")
        before = copy.deepcopy(m)
        if unit_cost is not None:
            m.unit_cost = q((m.on_hand * m.unit_cost + qty * unit_cost) / (m.on_hand + qty))
        m.on_hand = q(m.on_hand + qty)
        self.ctx.log("receive", "material", m.code, before, m)
        return m

    def adjust(self, code: str, delta: float, reason: str) -> Material:
        """Stock-count correction. May not drop stock below what is reserved."""
        m = self.get(code)
        if not reason.strip():
            raise ValidationError("adjustment reason required")
        if m.on_hand + delta < m.reserved - EPS:
            raise InsufficientStockError(
                f"{m.code}: adjustment would leave {q(m.on_hand + delta)} < reserved {m.reserved}")
        before = copy.deepcopy(m)
        m.on_hand = q(m.on_hand + delta)
        self.ctx.log("adjust", "material", m.code, before, {"material": m, "reason": reason})
        return m

    def available(self, code: str) -> float:
        m = self.get(code)
        return q(m.on_hand - m.reserved)

    def low_stock(self) -> List[Material]:
        return sorted((m for m in self.ctx.materials.values() if m.on_hand - m.reserved <= m.reorder_level + EPS
                       and m.reorder_level > 0), key=lambda m: m.code)

    def inventory_value(self) -> float:
        return q(sum(m.on_hand * m.unit_cost for m in self.ctx.materials.values()))


# ---- shared primitives for Material / Product (both expose on_hand, reserved) --------
def reserve(item, qty: float, label: str):
    if qty > item.on_hand - item.reserved + EPS:
        raise InsufficientStockError(
            f"{label}: need {q(qty)}, free {q(item.on_hand - item.reserved)}")
    item.reserved = q(item.reserved + qty)


def unreserve(item, qty: float):
    item.reserved = q(max(0.0, item.reserved - qty))


def issue(item, qty: float, own_reserved: float, label: str) -> Tuple[float, float]:
    """Remove qty from stock, drawing on the caller's reservation first.

    Returns (new_own_reserved, reservation_used). Raises if free stock is short.
    """
    from_res = min(qty, own_reserved)
    extra = qty - from_res
    free_after = item.on_hand - item.reserved  # others' reservations already excluded from free
    if extra > free_after + EPS:
        raise InsufficientStockError(f"{label}: need {q(qty)}, available {q(from_res + free_after)}")
    item.on_hand = q(item.on_hand - qty)
    item.reserved = q(item.reserved - from_res)
    return q(own_reserved - from_res), from_res
