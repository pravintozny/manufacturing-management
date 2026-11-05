import unittest
from datetime import timedelta

from helpers import MON, build_plant

from mfg.errors import DependencyError, InsufficientStockError, StateError, ValidationError


class ExecutionBase(unittest.TestCase):
    def setUp(self):
        self.p = build_plant()
        self.due = MON + timedelta(days=30)
        self.top = self.p.planning.create_order("TOP-1", 10, self.due)

    def run_top(self, good=10, **kw):
        pl, ex = self.p.planning, self.p.execution
        pl.release(self.top.id)
        ex.start(self.top.id)
        ex.consume(self.top.id, "PLY", 21)
        ex.report_progress(self.top.id, good=good, **kw)
        return self.top


class ExecutionTests(ExecutionBase):
    def test_lifecycle_and_costs(self):
        o = self.run_top()
        self.p.execution.complete(o.id)
        # material 21*10=210; hours: setup 1h + 10*0.5h = 6h; labor 15/h, machine 20/h
        c = self.p.costing.order_cost(o.id)
        self.assertEqual((c["material"], c["labor"], c["machine"]), (210.0, 90.0, 120.0))
        self.assertEqual(c["total"], 420.0)
        self.assertEqual(c["cost_per_unit"], 42.0)
        top = self.p.products.get("TOP-1")
        self.assertEqual((top.on_hand, top.avg_cost), (10, 42.0))
        self.assertEqual(self.p.inventory.get("PLY").on_hand, 79)
        self.assertEqual(self.p.inventory.get("PLY").reserved, 0)
        self.assertEqual(o.status, "COMPLETED")

    def test_parent_waits_for_child_then_uses_actual_subassembly_cost(self):
        desk = self.p.planning.create_order("DESK-1", 10, self.due)
        child = self.p.planning.get(desk.children[0])
        self.p.planning.release_tree(desk.id)
        with self.assertRaises(DependencyError):
            self.p.execution.start(desk.id)
        ex = self.p.execution
        ex.start(child.id)
        ex.consume(child.id, "PLY", 21)
        ex.report_progress(child.id, good=10)
        ex.complete(child.id)
        ex.start(desk.id)
        self.assertEqual(self.p.products.get("TOP-1").on_hand, 0)
        self.assertEqual(desk.material_cost, 10 * 42.0)  # sub-assembly valued at its actual cost
        ex.consume(desk.id, "SCREW", 80)
        ex.consume(desk.id, "PAINT", 5.5)
        ex.report_progress(desk.id, good=10)
        ex.complete(desk.id)
        self.assertEqual(self.p.products.get("DESK-1").on_hand, 10)
        self.assertEqual(self.p.inventory.get("SCREW").on_hand, 1920)

    def test_start_requires_release_and_children_stock(self):
        with self.assertRaises(StateError):
            self.p.execution.start(self.top.id)
        with self.assertRaises(StateError):
            self.p.execution.consume(self.top.id, "PLY", 1)

    def test_child_short_yield_blocks_parent_start(self):
        desk = self.p.planning.create_order("DESK-1", 10, self.due)
        child = desk.children[0]
        self.p.planning.release_tree(desk.id)
        ex = self.p.execution
        ex.start(child)
        ex.report_progress(child, good=8, rejected=2)
        ex.complete(child, short_close=True)
        with self.assertRaises(InsufficientStockError):
            ex.start(desk.id)
        self.assertEqual(desk.status, "RELEASED")  # unchanged

    def test_consumption_validations(self):
        pl, ex = self.p.planning, self.p.execution
        pl.release(self.top.id)
        ex.start(self.top.id)
        with self.assertRaises(ValidationError):
            ex.consume(self.top.id, "SCREW", 1)    # not in BOM
        with self.assertRaises(ValidationError):
            ex.consume(self.top.id, "PLY", 26)     # > 120% of 21
        with self.assertRaises(ValidationError):
            ex.consume(self.top.id, "PLY", 0)
        ex.consume(self.top.id, "PLY", 25)         # within tolerance (25.2)
        self.assertEqual(self.p.inventory.get("PLY").on_hand, 75)

    def test_consumption_cannot_take_other_orders_reservation(self):
        other = self.p.planning.create_order("TOP-1", 40, self.due)  # 84 PLY
        self.p.planning.release(other.id)
        with self.assertRaises(InsufficientStockError):
            self.p.planning.release(self.top.id)  # needs 21, only 16 free
        self.assertEqual(self.top.status, "PLANNED")

    def test_progress_validation(self):
        self.p.planning.release(self.top.id)
        self.p.execution.start(self.top.id)
        with self.assertRaises(ValidationError):
            self.p.execution.report_progress(self.top.id, good=11)
        with self.assertRaises(ValidationError):
            self.p.execution.report_progress(self.top.id)
        with self.assertRaises(ValidationError):
            self.p.execution.report_progress(self.top.id, good=-1, rejected=3)
        pr = self.p.execution.progress(self.top.id)
        self.assertEqual(pr["percent_complete"], 0)

    def test_complete_requires_full_qty_unless_short_closed(self):
        self.run_top(good=7, rejected=1)
        with self.assertRaises(StateError):
            self.p.execution.complete(self.top.id)
        self.p.execution.complete(self.top.id, short_close=True)
        self.assertTrue(self.top.short_closed)
        self.assertEqual(self.p.products.get("TOP-1").on_hand, 7)
        # rejected unit cost absorbed by good units: 1h setup + 8*0.5h = 5h
        self.assertAlmostEqual(self.top.unit_cost, (210 + 5 * 35) / 7, places=5)
        self.assertEqual(self.p.inventory.get("PLY").reserved, 0)  # unused reservation freed

    def test_cancel_after_start_refused(self):
        self.p.planning.release(self.top.id)
        self.p.execution.start(self.top.id)
        with self.assertRaises(StateError):
            self.p.planning.cancel(self.top.id)


class QualityTests(ExecutionBase):
    def setUp(self):
        super().setUp()
        self.p.planning.release(self.top.id)
        self.p.execution.start(self.top.id)
        self.p.execution.consume(self.top.id, "PLY", 21)
        self.q = self.p.quality

    def test_defective_blocks_completion_until_disposed(self):
        self.p.execution.report_progress(self.top.id, good=7, defective=3, defect_code="chip")
        self.assertEqual(self.top.defective, 3)
        with self.assertRaises(StateError):
            self.p.execution.complete(self.top.id)
        d = self.q.defects(self.top.id)[0]
        self.assertEqual(d.code, "CHIP")
        self.q.dispose(d.id, "accept", 1)
        self.q.dispose(d.id, "rework", 1)
        self.q.dispose(d.id, "reject", 1)
        self.assertEqual((self.top.good, self.top.rework_pending, self.top.rejected, self.top.defective),
                         (8, 1, 1, 0))
        with self.assertRaises(StateError):  # rework still pending
            self.p.execution.complete(self.top.id, short_close=True)
        self.q.complete_rework(self.top.id, fixed=1, cost=12.5)
        self.assertEqual(self.top.good, 9)
        self.p.execution.complete(self.top.id, short_close=True)
        self.assertEqual(self.top.rework_cost, 12.5)
        self.assertAlmostEqual(self.p.costing.order_cost(self.top.id)["cost_per_unit"], (210 + 6 * 35 + 12.5) / 9, places=4)

    def test_dispose_validation(self):
        self.p.execution.report_progress(self.top.id, good=5, defective=2, severity="critical")
        d = self.q.defects()[0]
        with self.assertRaises(ValidationError):
            self.q.dispose(d.id, "accept", 1)     # critical
        with self.assertRaises(ValidationError):
            self.q.dispose(d.id, "reject", 3)     # > open
        with self.assertRaises(ValidationError):
            self.q.dispose(d.id, "scrap", 1)
        with self.assertRaises(ValidationError):
            self.q.complete_rework(self.top.id, fixed=1)  # nothing pending

    def test_inspection_moves_good_units_to_defects(self):
        self.p.execution.report_progress(self.top.id, good=10)
        ins = self.q.inspect(self.top.id, "alice", 10, [{"code": "scratch", "qty": 2, "severity": "major"}])
        self.assertFalse(ins.passed)
        self.assertEqual((self.top.good, self.top.defective), (8, 2))
        self.assertEqual(self.q.pareto(), [("SCRATCH", 2.0)])
        ok = self.q.inspect(self.top.id, "alice", 8)
        self.assertTrue(ok.passed)
        with self.assertRaises(ValidationError):
            self.q.inspect(self.top.id, "alice", 9)   # more than good
        with self.assertRaises(ValidationError):
            self.q.inspect(self.top.id, "alice", 1, [{"code": "x", "qty": 2}])


if __name__ == "__main__":
    unittest.main()
