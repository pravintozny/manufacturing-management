"""Production planning: orders, dependency-aware sub-assembly orders, finite-capacity scheduling."""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Dict, List, Optional

from .bom import BomService, Node
from .context import EPS, Context, q
from .errors import CapacityError, DependencyError, InsufficientStockError, StateError, ValidationError
from .inventory import InventoryService, reserve, unreserve
from .models import (CANCELLED, COMPLETED, IN_PROGRESS, OPEN_STATUSES, PLANNED, RELEASED,
                     ProductionOrder, ScheduledOp)
from .workcenters import WorkCenterService


class PlanningService:
    def __init__(self, ctx: Context, boms: BomService, wcs: WorkCenterService):
        self.ctx, self.boms, self.wcs = ctx, boms, wcs

    # creation ----------------------------------------------------------------
    def create_order(self, sku: str, qty: float, due_date: date,
                     start_date: Optional[date] = None) -> ProductionOrder:
        """Create an order for `sku`. Sub-assemblies not covered by free stock get their own
        child orders, scheduled first; the parent starts after its children finish."""
        sku = sku.upper()
        p = self.ctx.get(self.ctx.products, sku, "product")
        if not p.active:
            raise StateError(f"{sku} is inactive")
        if qty <= 0:
            raise ValidationError("order qty must be > 0")
        start = start_date or self.ctx.today()
        if due_date < start:
            raise ValidationError("due date precedes start date")
        avail = {s: pr.on_hand - pr.reserved for s, pr in self.ctx.products.items()}
        tree = self.boms.build_tree(sku, qty, avail)
        # Validate everything schedulable first so a failure leaves no partial orders.
        created: List[ProductionOrder] = []
        try:
            root = self._materialize(tree, due_date, start, None, created)
        except Exception:
            for o in reversed(created):
                self._unbook(o)
                del self.ctx.orders[o.id]
            raise
        for o in created:
            self.ctx.log("create", "production_order", o.id, after=o)
        return root

    def _materialize(self, node: Node, due: date, start: date, parent_id, created) -> ProductionOrder:
        order = ProductionOrder(id=self.ctx.next_id("PO"), sku=node.sku, qty=node.qty, due_date=due,
                                start_date=start, bom_version=node.version, parent_id=parent_id,
                                material_plan=dict(node.materials), product_plan=dict(node.product_needs),
                                product_pulls=dict(node.pulls))
        self.ctx.orders[order.id] = order
        created.append(order)
        earliest = start
        for child in node.children:
            c = self._materialize(child, due, start, order.id, created)
            order.children.append(c.id)
            end = c.planned_end
            if end is not None:
                earliest = max(earliest, end + timedelta(days=1))
        order.start_date = earliest
        self._schedule(order, earliest)
        return order

    def _schedule(self, order: ProductionOrder, earliest: date):
        bom = self.boms.get(order.sku, order.bom_version)
        cursor = earliest
        booked = []
        try:
            for op in bom.operations:
                hours = (op.setup_minutes + op.minutes_per_unit * order.qty) / 60.0
                sop = ScheduledOp(op.seq, op.work_center, q(hours), cursor, cursor)
                remaining, day, guard = hours, cursor, 0
                while remaining > EPS:
                    free = self.wcs.free_capacity(op.work_center, day)
                    take = min(free, remaining)
                    if take > EPS:
                        self.wcs.book(op.work_center, day, take)
                        booked.append((op.work_center, day, take))
                        sop.slices[day] = q(take)
                        if len(sop.slices) == 1:
                            sop.start = day
                        sop.end = day
                        remaining -= take
                    if remaining > EPS:
                        day += timedelta(days=1)
                        guard += 1
                        if guard > 3650:
                            raise CapacityError(f"{op.work_center} has no capacity for {order.sku}")
                if hours <= EPS:
                    sop.start = sop.end = cursor
                order.schedule.append(sop)
                cursor = sop.end
        except Exception:
            for wc, day, h in booked:
                self.wcs.unbook(wc, day, h)
            order.schedule.clear()
            raise

    def _unbook(self, order: ProductionOrder):
        for sop in order.schedule:
            for day, h in sop.slices.items():
                self.wcs.unbook(sop.work_center, day, h)

    # queries -----------------------------------------------------------------
    def get(self, order_id: str) -> ProductionOrder:
        return self.ctx.get(self.ctx.orders, order_id, "production order")

    def list(self, status: Optional[str] = None) -> List[ProductionOrder]:
        return sorted((o for o in self.ctx.orders.values() if status is None or o.status == status),
                      key=lambda o: (o.start_date, o.id))

    def tree_ids(self, order_id: str) -> List[str]:
        o = self.get(order_id)
        out = [o.id]
        for c in o.children:
            out += self.tree_ids(c)
        return out

    def material_requirements(self, order_id: str, include_children: bool = True) -> Dict[str, float]:
        """Raw-material requirement of an order (and, optionally, its sub-assembly orders)."""
        out: Dict[str, float] = {}
        ids = self.tree_ids(order_id) if include_children else [order_id]
        for oid in ids:
            for k, v in self.get(oid).material_plan.items():
                out[k] = q(out.get(k, 0.0) + v)
        return out

    # state transitions -------------------------------------------------------------
    def release(self, order_id: str) -> ProductionOrder:
        """Validate stock and reserve materials / sub-assembly stock for this order."""
        o = self.get(order_id)
        if o.status != PLANNED:
            raise StateError(f"{o.id} is {o.status}; only PLANNED orders can be released")
        for cid in o.children:
            if self.get(cid).status == CANCELLED:
                raise DependencyError(f"{o.id}: child order {cid} is cancelled")
        short = []
        for code, need in o.material_plan.items():
            m = self.ctx.materials[code]
            if need > m.on_hand - m.reserved + EPS:
                short.append(f"{code}: need {need}, free {q(m.on_hand - m.reserved)}")
        for sku, need in o.product_pulls.items():
            p = self.ctx.products[sku]
            if need > p.on_hand - p.reserved + EPS:
                short.append(f"{sku}: need {need}, free {q(p.on_hand - p.reserved)}")
        if short:
            raise InsufficientStockError(f"{o.id} cannot be released: " + "; ".join(short))
        before = self._brief(o)
        for code, need in o.material_plan.items():
            reserve(self.ctx.materials[code], need, code)
            o.reserved_materials[code] = need
        for sku, need in o.product_pulls.items():
            reserve(self.ctx.products[sku], need, sku)
            o.reserved_products[sku] = need
        o.status = RELEASED
        self.ctx.log("release", "production_order", o.id, before, self._brief(o))
        return o

    def release_tree(self, order_id: str) -> List[ProductionOrder]:
        """Release an order and its not-yet-released descendants (children first)."""
        out = []
        for oid in reversed(self.tree_ids(order_id)):
            if self.get(oid).status == PLANNED:
                out.append(self.release(oid))
        return out

    def cancel(self, order_id: str) -> ProductionOrder:
        o = self.get(order_id)
        if o.status not in (PLANNED, RELEASED):
            raise StateError(f"{o.id} is {o.status}; cannot cancel")
        if o.parent_id and self.get(o.parent_id).status in OPEN_STATUSES:
            raise DependencyError(f"{o.id} feeds open order {o.parent_id}; cancel the parent first")
        before = self._brief(o)
        for code, qty in o.reserved_materials.items():
            unreserve(self.ctx.materials[code], qty)
        for sku, qty in o.reserved_products.items():
            unreserve(self.ctx.products[sku], qty)
        o.reserved_materials.clear()
        o.reserved_products.clear()
        self._unbook(o)
        o.status = CANCELLED
        self.ctx.log("cancel", "production_order", o.id, before, self._brief(o))
        return o

    @staticmethod
    def _brief(o: ProductionOrder) -> dict:
        return {"status": o.status, "reserved_materials": dict(o.reserved_materials),
                "reserved_products": dict(o.reserved_products)}

    # schedule views ------------------------------------------------------------------
    def schedule(self, start: Optional[date] = None, end: Optional[date] = None) -> List[dict]:
        rows = []
        for o in self.list():
            if o.status == CANCELLED:
                continue
            for s in o.schedule:
                if (start and s.end < start) or (end and s.start > end):
                    continue
                rows.append({"order": o.id, "sku": o.sku, "op": s.seq, "work_center": s.work_center,
                             "start": s.start, "end": s.end, "hours": s.hours, "late": o.late})
        return sorted(rows, key=lambda r: (r["start"], r["order"], r["op"]))
