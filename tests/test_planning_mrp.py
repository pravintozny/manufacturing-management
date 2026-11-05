import unittest
from datetime import date, timedelta

from helpers import MON, build_plant

from mfg.errors import CapacityError, DependencyError, InsufficientStockError, StateError, ValidationError


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.p = build_plant()
        self.due = MON + timedelta(days=14)

    def test_dependency_aware_order_tree(self):
        o = self.p.planning.create_order("DESK-1", 10, self.due)
        self.assertEqual(len(o.children), 1)
        child = self.p.planning.get(o.children[0])
        self.assertEqual((child.sku, child.qty), ("TOP-1", 10))
        self.assertEqual(o.product_plan, {"TOP-1": 10})
        self.assertEqual(o.product_pulls, {})
        self.assertEqual(child.material_plan, {"PLY": 21.0})
        self.assertEqual(o.material_plan, {"SCREW": 80.0, "PAINT": 5.5})
        self.assertEqual(self.p.planning.material_requirements(o.id), {"PLY": 21.0, "SCREW": 80.0, "PAINT": 5.5})
        self.assertEqual(self.p.planning.material_requirements(o.id, False), {"SCREW": 80.0, "PAINT": 5.5})
        self.assertGreater(o.start_date, child.planned_end)  # parent waits for child

    def test_existing_subassembly_stock_reduces_child_order(self):
        top = self.p.products.get("TOP-1")
        top.on_hand = 4
        o = self.p.planning.create_order("DESK-1", 10, self.due)
        self.assertEqual(o.product_pulls, {"TOP-1": 4})
        self.assertEqual(self.p.planning.get(o.children[0]).qty, 6)
        top.on_hand = 50  # fully covered: no child at all
        o2 = self.p.planning.create_order("DESK-1", 5, self.due)
        self.assertEqual(o2.children, [])

    def test_schedule_respects_capacity_and_spills_over_days(self):
        # ASM: 8h * 50% = 4h/day. 10 desks = (30 + 600)/60 = 10.5h -> 3 days (4+4+2.5)
        o = self.p.planning.create_order("DESK-1", 10, self.due)
        op = o.schedule[0]
        self.assertEqual(op.hours, 10.5)
        self.assertEqual(len(op.slices), 3)
        self.assertTrue(all(h <= 4.0 + 1e-9 for h in op.slices.values()))
        # second order competes for the same capacity: starts no earlier than first one's last day
        o2 = self.p.planning.create_order("DESK-1", 1, self.due)
        total_per_day = {}
        for ord_ in (o, o2):
            for d, h in ord_.schedule[0].slices.items():
                total_per_day[d] = total_per_day.get(d, 0) + h
        self.assertTrue(all(h <= 4.0 + 1e-9 for h in total_per_day.values()))

    def test_weekends_and_downtime_skipped(self):
        fri = date(2026, 11, 6)
        self.p.work_centers.add_downtime("CUT", fri)
        o = self.p.planning.create_order("TOP-1", 30, self.due, start_date=fri)  # 15.5h at 8h/day
        days = sorted(o.schedule[0].slices)
        self.assertEqual(days[0], date(2026, 11, 9))  # Fri down, weekend off -> Monday
        self.assertTrue(all(d.weekday() < 5 for d in days))

    def test_late_flag(self):
        o = self.p.planning.create_order("DESK-1", 10, MON + timedelta(days=1))
        self.assertTrue(o.late)

    def test_zero_capacity_raises_and_leaves_no_orders(self):
        self.p.work_centers.update("ASM", availability_pct=0)
        with self.assertRaises(CapacityError):
            self.p.planning.create_order("DESK-1", 1, self.due)
        self.assertEqual(self.p.planning.list(), [])
        self.assertEqual(sum(sum(d.values()) for d in self.p.work_centers.load.values()), 0)

    def test_validation(self):
        with self.assertRaises(ValidationError):
            self.p.planning.create_order("DESK-1", 0, self.due)
        with self.assertRaises(ValidationError):
            self.p.planning.create_order("DESK-1", 1, MON - timedelta(days=1))

    def test_release_reserves_and_cancel_frees(self):
        o = self.p.planning.create_order("DESK-1", 10, self.due)
        self.p.planning.release_tree(o.id)
        self.assertEqual(self.p.inventory.available("SCREW"), 1920)
        self.assertEqual(self.p.inventory.available("PLY"), 79)
        with self.assertRaises(DependencyError):
            self.p.planning.cancel(o.children[0])  # feeds an open parent
        self.p.planning.cancel(o.id)
        self.p.planning.cancel(o.children[0])
        self.assertEqual(self.p.inventory.available("SCREW"), 2000)
        self.assertEqual(self.p.inventory.available("PLY"), 100)
        self.assertEqual(sum(sum(d.values()) for d in self.p.work_centers.load.values()), 0)
        with self.assertRaises(StateError):
            self.p.planning.release(o.id)

    def test_release_blocked_by_insufficient_stock_is_atomic(self):
        o = self.p.planning.create_order("DESK-1", 400, self.due)  # SCREW 3200 > 2000
        with self.assertRaises(InsufficientStockError) as cm:
            self.p.planning.release(o.id)
        self.assertIn("SCREW", str(cm.exception))
        self.assertEqual(self.p.inventory.available("PAINT"), 30)  # nothing reserved
        self.assertEqual(o.status, "PLANNED")


class MrpTests(unittest.TestCase):
    def setUp(self):
        self.p = build_plant()
        self.due = MON + timedelta(days=30)

    def line(self, lines, code):
        return next(l for l in lines if l.material == code)

    def test_no_shortage_but_replenish_below_reorder_level(self):
        self.p.planning.create_order("DESK-1", 10, self.due)  # PLY need 21 of 100 -> 79 left (>20)
        lines = self.p.mrp.run()
        ply = self.line(lines, "PLY")
        self.assertEqual((ply.required, ply.available, ply.shortage, ply.reason), (21.0, 100.0, 0.0, "OK"))
        self.assertEqual(self.p.mrp.shortages(), [])

    def test_shortage_and_purchase_recommendation(self):
        # TOP 40 -> PLY 84 required; stock 100 - but a second order for 40 more -> 168 required, 68 short
        self.p.planning.create_order("TOP-1", 40, self.due)
        self.p.planning.create_order("TOP-1", 40, self.due + timedelta(days=1))
        ply = self.line(self.p.mrp.run(), "PLY")
        self.assertEqual(ply.required, 168.0)
        self.assertEqual(ply.shortage, 68.0)
        # want = shortage 68 + reorder_level 20 = 88 -> lots of 50 -> 100
        self.assertEqual(ply.recommended_qty, 100.0)
        self.assertEqual(ply.reason, "SHORTAGE")
        self.assertEqual(ply.estimated_cost, 1000.0)
        self.assertEqual(ply.supplier_id, "S1")
        self.assertEqual(ply.order_by, ply.need_by - timedelta(days=5))
        self.assertEqual([l.material for l in self.p.mrp.shortages()], ["PLY"])

    def test_earlier_due_date_claims_stock_first(self):
        late = self.p.planning.create_order("TOP-1", 40, self.due + timedelta(days=5))
        early = self.p.planning.create_order("TOP-1", 40, self.due)
        # only 100 PLY; early takes 84, late is short 68 -> need_by is the late order's start
        ply = self.line(self.p.mrp.run(), "PLY")
        self.assertEqual(ply.shortage, 68.0)
        self.assertEqual(ply.need_by, late.start_date)
        self.assertIsNotNone(early)

    def test_released_orders_not_double_counted(self):
        o = self.p.planning.create_order("TOP-1", 40, self.due)
        self.p.planning.release(o.id)
        lines = self.p.mrp.run()
        ply = self.line(lines, "PLY")
        self.assertEqual((ply.required, ply.available, ply.shortage), (0.0, 16.0, 0.0))
        self.assertEqual(ply.reason, "BELOW_REORDER_LEVEL")  # 16 < 20
        self.assertEqual(ply.recommended_qty, 50.0)

    def test_check_order(self):
        o = self.p.planning.create_order("TOP-1", 60, self.due)
        self.assertEqual(self.p.mrp.check_order(o.id)["PLY"],
                         {"required": 126.0, "available": 100.0, "shortage": 26.0})


if __name__ == "__main__":
    unittest.main()
