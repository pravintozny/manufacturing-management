"""Cost management: actual order costs, cost per unit, variance against standard."""
from __future__ import annotations

from typing import Dict, Optional

from .context import Context, q
from .models import COMPLETED


class CostService:
    def __init__(self, ctx: Context, planning, boms):
        self.ctx, self.planning, self.boms = ctx, planning, boms

    def order_cost(self, order_id: str) -> Dict[str, Optional[float]]:
        o = self.planning.get(order_id)
        total = q(o.material_cost + o.labor_cost + o.machine_cost + o.rework_cost)
        return {"material": o.material_cost, "labor": o.labor_cost, "machine": o.machine_cost,
                "rework": o.rework_cost, "total": total,
                "good_qty": o.good,
                "cost_per_unit": q(total / o.good) if o.good > 0 else None}

    def standard_vs_actual(self, order_id: str) -> Dict[str, float]:
        """Per-unit variance (positive = over standard) for a completed order."""
        o = self.planning.get(order_id)
        std = self.boms.standard_cost(o.sku, o.qty, o.bom_version)["total"]
        actual = self.order_cost(order_id)["cost_per_unit"]
        if o.status != COMPLETED or actual is None:
            return {"standard": std, "actual": None, "variance": None}
        return {"standard": std, "actual": actual, "variance": q(actual - std)}
