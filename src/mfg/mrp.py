"""Material Requirements Planning: requirements, availability, shortages, purchase advice."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional

from .context import EPS, Context, q
from .models import PLANNED, ProductionOrder


@dataclass
class MrpLine:
    material: str
    required: float          # demand from not-yet-released orders
    available: float         # free stock (on hand - reserved)
    shortage: float
    projected: float         # available - required (may be negative)
    recommended_qty: float
    reason: str              # SHORTAGE | BELOW_REORDER_LEVEL | OK
    need_by: Optional[date]
    order_by: Optional[date]
    supplier_id: Optional[str]
    estimated_cost: float


class MrpService:
    def __init__(self, ctx: Context, planning):
        self.ctx, self.planning = ctx, planning

    def run(self, order_ids: Optional[List[str]] = None) -> List[MrpLine]:
        """Net requirements of PLANNED orders against free stock.

        Released orders already hold reservations, so they are not double counted.
        Orders are served in due-date order: earlier orders claim stock first.
        """
        orders: List[ProductionOrder] = [o for o in self.ctx.orders.values() if o.status == PLANNED
                                         and (order_ids is None or o.id in order_ids)]
        orders.sort(key=lambda o: (o.due_date, o.start_date, o.id))
        required: Dict[str, float] = {}
        need_by: Dict[str, date] = {}
        free = {c: m.on_hand - m.reserved for c, m in self.ctx.materials.items()}
        shortage: Dict[str, float] = {}
        for o in orders:
            for code, qty in o.material_plan.items():
                required[code] = q(required.get(code, 0.0) + qty)
                take = min(max(free[code], 0.0), qty)
                free[code] -= take
                miss = qty - take
                if miss > EPS:
                    shortage[code] = q(shortage.get(code, 0.0) + miss)
                    # the first order that misses material defines when it is needed
                    need_by[code] = min(need_by.get(code, o.start_date), o.start_date)
        lines = []
        for code, m in sorted(self.ctx.materials.items()):
            req = required.get(code, 0.0)
            avail = q(m.on_hand - m.reserved)
            projected = q(avail - req)
            short = shortage.get(code, 0.0)
            want = max(0.0, m.reorder_level - projected) if (m.reorder_level > 0 or short > 0) else 0.0
            if short > EPS:
                want = max(want, short)
            rec = self._round_to_lot(want, m.reorder_qty)
            reason = "SHORTAGE" if short > EPS else ("BELOW_REORDER_LEVEL" if rec > EPS else "OK")
            lead = self.ctx.suppliers[m.supplier_id].lead_time_days if m.supplier_id else 0
            nb = need_by.get(code)
            ob = (nb - timedelta(days=lead)) if nb else None
            if reason == "OK" and req <= EPS:
                continue  # nothing to say about idle, healthy materials
            lines.append(MrpLine(code, req, avail, short, projected, rec, reason, nb, ob,
                                 m.supplier_id, q(rec * m.unit_cost)))
        return lines

    @staticmethod
    def _round_to_lot(qty: float, lot: float) -> float:
        if qty <= EPS:
            return 0.0
        return q(math.ceil(qty / lot - EPS) * lot) if lot > 0 else q(qty)

    def shortages(self, order_ids: Optional[List[str]] = None) -> List[MrpLine]:
        return [l for l in self.run(order_ids) if l.shortage > EPS]

    def purchase_recommendations(self) -> List[MrpLine]:
        return [l for l in self.run() if l.recommended_qty > EPS]

    def check_order(self, order_id: str) -> Dict[str, dict]:
        """Per-material feasibility of one order tree against current free stock."""
        req = self.planning.material_requirements(order_id)
        out = {}
        for code, need in req.items():
            m = self.ctx.materials[code]
            free = q(m.on_hand - m.reserved)
            out[code] = {"required": need, "available": free, "shortage": q(max(0.0, need - free))}
        return out
