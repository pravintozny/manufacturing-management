"""Production execution: start, consume materials, report output, complete."""
from __future__ import annotations

from datetime import date
from typing import Dict, Optional

from .context import EPS, Context, q
from .errors import DependencyError, StateError, ValidationError
from .inventory import issue
from .models import COMPLETED, IN_PROGRESS, RELEASED, ProductionOrder

OVERCONSUMPTION_TOLERANCE = 0.20   # consumption beyond 120% of plan is refused


class ExecutionService:
    def __init__(self, ctx: Context, planning, boms, quality):
        self.ctx, self.planning, self.boms, self.quality = ctx, planning, boms, quality

    def _in_progress(self, order_id: str) -> ProductionOrder:
        o = self.planning.get(order_id)
        if o.status != IN_PROGRESS:
            raise StateError(f"{o.id} is {o.status}; expected IN_PROGRESS")
        return o

    def start(self, order_id: str) -> ProductionOrder:
        """Start a RELEASED order once all sub-assembly orders are complete.
        Sub-assemblies are issued from stock at this point."""
        o = self.planning.get(order_id)
        if o.status != RELEASED:
            raise StateError(f"{o.id} is {o.status}; only RELEASED orders can start")
        pending = [c for c in o.children if self.planning.get(c).status != COMPLETED]
        if pending:
            raise DependencyError(f"{o.id} waits for sub-assembly orders: {', '.join(pending)}")
        # validate first so the issue is all-or-nothing
        for sku, need in o.product_plan.items():
            p = self.ctx.products[sku]
            own = o.reserved_products.get(sku, 0.0)
            if need > own + (p.on_hand - p.reserved) + EPS:
                from .errors import InsufficientStockError
                raise InsufficientStockError(
                    f"{o.id}: sub-assembly {sku} need {need}, available {q(own + p.on_hand - p.reserved)}")
        before = {"status": o.status}
        for sku, need in o.product_plan.items():
            p = self.ctx.products[sku]
            o.reserved_products[sku], _ = issue(p, need, o.reserved_products.get(sku, 0.0), sku)
            o.consumed_products[sku] = need
            o.material_cost = q(o.material_cost + need * p.avg_cost)
        o.status = IN_PROGRESS
        self.ctx.log("start", "production_order", o.id, before, {"status": o.status})
        return o

    def consume(self, order_id: str, material: str, qty: float) -> ProductionOrder:
        """Issue raw material to the order; refuses to drive stock negative."""
        o = self._in_progress(order_id)
        code = material.upper()
        if qty <= 0:
            raise ValidationError("consumption qty must be > 0")
        if code not in o.material_plan:
            raise ValidationError(f"{code} is not a planned component of {o.id}")
        total = o.consumed_materials.get(code, 0.0) + qty
        limit = o.material_plan[code] * (1 + OVERCONSUMPTION_TOLERANCE)
        if total > limit + EPS:
            raise ValidationError(f"{code}: consumption {q(total)} exceeds limit {q(limit)}")
        m = self.ctx.materials[code]
        before = {"on_hand": m.on_hand, "consumed": o.consumed_materials.get(code, 0.0)}
        o.reserved_materials[code], _ = issue(m, qty, o.reserved_materials.get(code, 0.0), code)
        o.consumed_materials[code] = q(total)
        o.material_cost = q(o.material_cost + qty * m.unit_cost)
        self.ctx.log("consume", "production_order", o.id, before,
                     {"material": code, "qty": qty, "on_hand": m.on_hand, "consumed": o.consumed_materials[code]})
        return o

    def report_progress(self, order_id: str, good: float = 0.0, rejected: float = 0.0,
                        defective: float = 0.0, defect_code: str = "UNSPECIFIED",
                        severity: str = "minor", hours: Optional[Dict[str, float]] = None,
                        on: Optional[date] = None) -> ProductionOrder:
        """Record units produced. `defective` units await disposition (accept/rework/reject).
        `hours` maps work center -> actual hours; defaults to standard time for the units processed."""
        o = self._in_progress(order_id)
        if min(good, rejected, defective) < 0 or good + rejected + defective <= 0:
            raise ValidationError("quantities must be >= 0 and not all zero")
        if o.good + o.defective + o.rework_pending + good + defective > o.qty + EPS:
            raise ValidationError(
                f"{o.id}: good+defective+rework would exceed planned qty {o.qty}")
        units = good + rejected + defective
        day = on or self.ctx.today()
        bom = self.boms.get(o.sku, o.bom_version)
        if hours is None:
            hours = {}
            for op in bom.operations:
                h = op.minutes_per_unit * units / 60.0 + (0 if o.setup_done else op.setup_minutes / 60.0)
                hours[op.work_center] = hours.get(op.work_center, 0.0) + h
        elif any(h < 0 for h in hours.values()):
            raise ValidationError("hours must be >= 0")
        for wc in hours:
            self.ctx.get(self.ctx.work_centers, wc.upper(), "work center")
        before = {"good": o.good, "rejected": o.rejected, "defective": o.defective}
        o.good = q(o.good + good)
        o.rejected = q(o.rejected + rejected)
        o.processed = q(o.processed + units)
        o.setup_done = True
        for wc, h in hours.items():
            wc = wc.upper()
            if h <= 0:
                continue
            w = self.ctx.work_centers[wc]
            o.hours_log.append((day, wc, q(h)))
            o.labor_cost = q(o.labor_cost + h * w.labor_rate)
            o.machine_cost = q(o.machine_cost + h * w.machine_rate)
        if defective > 0:
            self.quality.log_defect(o, defect_code, defective, severity, "production",
                                    next(iter(hours), None))
        self.ctx.log("progress", "production_order", o.id, before,
                     {"good": o.good, "rejected": o.rejected, "defective": o.defective, "hours": hours})
        return o

    def complete(self, order_id: str, short_close: bool = False) -> ProductionOrder:
        """Close the order and put good units into finished stock at actual unit cost."""
        o = self._in_progress(order_id)
        if o.defective > EPS or o.rework_pending > EPS:
            raise StateError(f"{o.id}: {o.defective} defective / {o.rework_pending} rework units unresolved")
        if o.good + EPS < o.qty and not short_close:
            raise StateError(f"{o.id}: only {o.good}/{o.qty} good; pass short_close=True to close short")
        p = self.ctx.products[o.sku]
        before = {"status": o.status, "stock": p.on_hand, "avg_cost": p.avg_cost}
        total = self.total_cost(o)
        if o.good > EPS:
            o.unit_cost = q(total / o.good)
            p.avg_cost = q((p.on_hand * p.avg_cost + o.good * o.unit_cost) / (p.on_hand + o.good))
            p.on_hand = q(p.on_hand + o.good)
        for code, qty in list(o.reserved_materials.items()):  # release unused reservations
            from .inventory import unreserve
            unreserve(self.ctx.materials[code], qty)
        o.reserved_materials.clear()
        o.short_closed = o.good + EPS < o.qty
        o.status = COMPLETED
        o.completed_on = self.ctx.today()
        self.ctx.log("complete", "production_order", o.id, before,
                     {"status": o.status, "good": o.good, "unit_cost": o.unit_cost, "stock": p.on_hand})
        return o

    @staticmethod
    def total_cost(o: ProductionOrder) -> float:
        return q(o.material_cost + o.labor_cost + o.machine_cost + o.rework_cost)

    def progress(self, order_id: str) -> dict:
        o = self.planning.get(order_id)
        return {"order": o.id, "status": o.status, "planned": o.qty, "good": o.good,
                "rejected": o.rejected, "defective": o.defective, "rework_pending": o.rework_pending,
                "percent_complete": q(100.0 * o.good / o.qty), "consumed": dict(o.consumed_materials)}
