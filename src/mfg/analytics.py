"""Production analytics. All figures are derived from recorded execution data."""
from __future__ import annotations

from datetime import date
from typing import Dict, Optional

from .context import EPS, Context, q
from .models import CANCELLED, PLANNED, RELEASED


def _ratio(n: float, d: float) -> Optional[float]:
    return q(n / d) if d > EPS else None


class AnalyticsService:
    def __init__(self, ctx: Context, boms, wcs):
        self.ctx, self.boms, self.wcs = ctx, boms, wcs

    def _executed(self, sku: Optional[str] = None):
        return [o for o in self.ctx.orders.values()
                if o.status not in (PLANNED, RELEASED, CANCELLED) and (sku is None or o.sku == sku.upper())]

    def production_efficiency(self, sku: Optional[str] = None) -> Optional[float]:
        """Standard hours earned by units processed / actual hours booked (1.0 = on standard)."""
        std = act = 0.0
        for o in self._executed(sku):
            bom = self.boms.get(o.sku, o.bom_version)
            run = sum(op.minutes_per_unit for op in bom.operations) / 60.0
            setup = sum(op.setup_minutes for op in bom.operations) / 60.0 if o.setup_done else 0.0
            std += run * o.processed + setup
            act += sum(h for _, _, h in o.hours_log)
        return _ratio(std, act)

    def material_utilization(self, sku: Optional[str] = None) -> Dict[str, Optional[float]]:
        """Theoretical usage (good units x BOM qty, no scrap allowance) / actual consumption, per material."""
        theo: Dict[str, float] = {}
        used: Dict[str, float] = {}
        for o in self._executed(sku):
            bom = self.boms.get(o.sku, o.bom_version)
            for c in bom.components:
                if c.kind == "material":
                    theo[c.ref] = theo.get(c.ref, 0.0) + c.qty_per_unit * o.good
            for code, qty in o.consumed_materials.items():
                used[code] = used.get(code, 0.0) + qty
        return {c: _ratio(theo.get(c, 0.0), used[c]) for c in sorted(used)}

    def overall_material_utilization(self) -> Optional[float]:
        """Cost-weighted utilization across all materials."""
        num = den = 0.0
        for code, u in self.material_utilization().items():
            if u is None:
                continue
            cost = self.ctx.materials[code].unit_cost
            used = sum(o.consumed_materials.get(code, 0.0) for o in self._executed())
            num += u * used * cost
            den += used * cost
        return _ratio(num, den)

    def defect_rates(self, sku: Optional[str] = None) -> Dict[str, Optional[float]]:
        processed = sum(o.processed for o in self._executed(sku))
        defects = sum(d.qty for d in self.ctx.defects.values()
                      if sku is None or self.ctx.orders[d.order_id].sku == sku.upper())
        rejected = sum(o.rejected for o in self._executed(sku))
        return {"processed": q(processed), "defect_rate": _ratio(defects, processed),
                "reject_rate": _ratio(rejected, processed),
                "first_pass_yield": _ratio(processed - defects, processed)}

    def production_costs(self) -> Dict[str, dict]:
        """Actual cost totals per SKU."""
        out: Dict[str, dict] = {}
        for o in self._executed():
            r = out.setdefault(o.sku, {"material": 0.0, "labor": 0.0, "machine": 0.0, "rework": 0.0,
                                       "total": 0.0, "good_qty": 0.0})
            for k, v in (("material", o.material_cost), ("labor", o.labor_cost),
                         ("machine", o.machine_cost), ("rework", o.rework_cost), ("good_qty", o.good)):
                r[k] = q(r[k] + v)
            r["total"] = q(r["material"] + r["labor"] + r["machine"] + r["rework"])
        for r in out.values():
            r["cost_per_unit"] = _ratio(r["total"], r["good_qty"])
        return out

    def work_center_utilization(self, start: date, end: date) -> Dict[str, Optional[float]]:
        """Actual booked hours / available hours in [start, end] for every work center."""
        out = {}
        for code in sorted(self.ctx.work_centers):
            actual = sum(h for o in self.ctx.orders.values() for d, wc, h in o.hours_log
                         if wc == code and start <= d <= end)
            out[code] = _ratio(actual, self.wcs.available_hours(code, start, end))
        return out

    def summary(self, start: date, end: date) -> dict:
        return {"efficiency": self.production_efficiency(),
                "material_utilization": self.overall_material_utilization(),
                "defects": self.defect_rates(), "costs": self.production_costs(),
                "work_center_utilization": self.work_center_utilization(start, end)}
