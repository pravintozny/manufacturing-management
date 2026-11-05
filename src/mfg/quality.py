"""Quality management: inspections, defect tracking, rework / rejection handling."""
from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional

from .context import EPS, Context, q
from .errors import StateError, ValidationError
from .models import IN_PROGRESS, DefectRecord, Inspection

SEVERITIES = ("minor", "major", "critical")
ACTIONS = ("accept", "rework", "reject")


class QualityService:
    def __init__(self, ctx: Context, planning):
        self.ctx, self.planning = ctx, planning

    def _order(self, order_id):
        o = self.planning.get(order_id)
        if o.status != IN_PROGRESS:
            raise StateError(f"{o.id} is {o.status}; quality actions need an IN_PROGRESS order")
        return o

    def log_defect(self, order, code: str, qty: float, severity: str, source: str,
                   work_center: Optional[str] = None) -> DefectRecord:
        """Register defective units in the order's awaiting-disposition pool."""
        if severity not in SEVERITIES:
            raise ValidationError(f"severity must be one of {SEVERITIES}")
        if qty <= 0 or not code.strip():
            raise ValidationError("defect needs a code and qty > 0")
        d = DefectRecord(self.ctx.next_id("DEF"), order.id, code.strip().upper(), q(qty), severity,
                         q(qty), source, work_center.upper() if work_center else None)
        self.ctx.defects[d.id] = d
        order.defective = q(order.defective + qty)
        self.ctx.log("create", "defect", d.id, after=d)
        return d

    def inspect(self, order_id: str, inspector: str, inspected_qty: float,
                defects: Optional[List[dict]] = None, on: Optional[date] = None) -> Inspection:
        """Inspect good output. Each defect dict: code, qty, severity(optional), work_center(optional).
        Units found defective move from `good` into the defective pool."""
        o = self._order(order_id)
        defects = defects or []
        if inspected_qty <= 0:
            raise ValidationError("inspected qty must be > 0")
        bad = sum(d["qty"] for d in defects)
        if bad > inspected_qty + EPS:
            raise ValidationError("defects exceed inspected qty")
        if inspected_qty > o.good + EPS:
            raise ValidationError(f"only {o.good} good units available to inspect")
        if bad > o.good + EPS:
            raise ValidationError("defects exceed good units")
        o.good = q(o.good - bad)
        recs = [self.log_defect(o, d["code"], d["qty"], d.get("severity", "minor"), "inspection",
                                d.get("work_center")) for d in defects]
        insp = Inspection(self.ctx.next_id("INS"), o.id, inspector, q(inspected_qty), q(bad),
                          bad <= EPS, on or self.ctx.today(), [r.id for r in recs])
        self.ctx.inspections[insp.id] = insp
        self.ctx.log("inspect", "inspection", insp.id, after=insp)
        return insp

    def dispose(self, defect_id: str, action: str, qty: float, reason: str = "") -> DefectRecord:
        """Decide the fate of defective units: accept (use-as-is), rework, or reject."""
        d = self.ctx.get(self.ctx.defects, defect_id, "defect")
        o = self._order(d.order_id)
        if action not in ACTIONS:
            raise ValidationError(f"action must be one of {ACTIONS}")
        if qty <= 0 or qty > d.open_qty + EPS:
            raise ValidationError(f"qty must be in (0, {d.open_qty}]")
        if action == "accept" and d.severity == "critical":
            raise ValidationError("critical defects cannot be accepted as-is")
        before = {"open_qty": d.open_qty, "good": o.good, "rejected": o.rejected,
                  "rework_pending": o.rework_pending}
        d.open_qty = q(d.open_qty - qty)
        o.defective = q(o.defective - qty)
        if action == "accept":
            o.good = q(o.good + qty)
        elif action == "rework":
            o.rework_pending = q(o.rework_pending + qty)
        else:
            o.rejected = q(o.rejected + qty)
        d.dispositions.append({"action": action, "qty": q(qty), "reason": reason, "on": self.ctx.today()})
        self.ctx.log("dispose", "defect", d.id, before, {"action": action, "qty": qty,
                     "open_qty": d.open_qty, "good": o.good, "rejected": o.rejected,
                     "rework_pending": o.rework_pending})
        return d

    def complete_rework(self, order_id: str, fixed: float, failed: float = 0.0,
                        cost: float = 0.0) -> None:
        """Close out reworked units: `fixed` rejoin good output, `failed` are rejected."""
        o = self._order(order_id)
        if fixed < 0 or failed < 0 or cost < 0 or fixed + failed <= 0:
            raise ValidationError("fixed/failed/cost must be non-negative and not both zero")
        if fixed + failed > o.rework_pending + EPS:
            raise ValidationError(f"only {o.rework_pending} units pending rework")
        before = {"rework_pending": o.rework_pending, "good": o.good, "rejected": o.rejected}
        o.rework_pending = q(o.rework_pending - fixed - failed)
        o.good = q(o.good + fixed)
        o.rejected = q(o.rejected + failed)
        o.rework_cost = q(o.rework_cost + cost)
        self.ctx.log("rework", "production_order", o.id, before, {
            "fixed": fixed, "failed": failed, "cost": cost, "rework_pending": o.rework_pending})

    # reporting ----------------------------------------------------------------------
    def defects(self, order_id: Optional[str] = None, open_only: bool = False) -> List[DefectRecord]:
        return [d for d in self.ctx.defects.values()
                if (order_id is None or d.order_id == order_id) and (not open_only or d.open_qty > EPS)]

    def pareto(self) -> List[tuple]:
        """Defect quantity by code, largest first."""
        tot: Dict[str, float] = {}
        for d in self.ctx.defects.values():
            tot[d.code] = q(tot.get(d.code, 0.0) + d.qty)
        return sorted(tot.items(), key=lambda kv: (-kv[1], kv[0]))
