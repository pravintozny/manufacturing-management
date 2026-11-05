import unittest
from datetime import timedelta

from helpers import MON, build_plant

from mfg.audit import AuditEntry


class AnalyticsAuditTests(unittest.TestCase):
    def setUp(self):
        self.p = build_plant()
        pl, ex = self.p.planning, self.p.execution
        self.o = pl.create_order("TOP-1", 10, MON + timedelta(days=20))
        pl.release(self.o.id)
        ex.start(self.o.id)
        ex.consume(self.o.id, "PLY", 21)
        ex.report_progress(self.o.id, good=8, rejected=1, defective=1, defect_code="warp",
                           hours={"CUT": 8.0}, on=MON)
        self.p.quality.dispose(self.p.quality.defects()[0].id, "reject", 1)
        ex.complete(self.o.id, short_close=True)

    def test_defect_rates(self):
        r = self.p.analytics.defect_rates()
        self.assertEqual(r["processed"], 10)
        self.assertEqual(r["defect_rate"], 0.1)
        self.assertEqual(r["reject_rate"], 0.2)
        self.assertEqual(r["first_pass_yield"], 0.9)

    def test_efficiency(self):
        # standard: 10 units*0.5h + 1h setup = 6h ; actual 8h
        self.assertAlmostEqual(self.p.analytics.production_efficiency(), 0.75)
        self.assertAlmostEqual(self.p.analytics.production_efficiency("TOP-1"), 0.75)
        self.assertIsNone(self.p.analytics.production_efficiency("DESK-1"))

    def test_material_utilization(self):
        # theoretical 8 good * 2 = 16 ; consumed 21
        self.assertAlmostEqual(self.p.analytics.material_utilization()["PLY"], 16 / 21, places=5)
        self.assertAlmostEqual(self.p.analytics.overall_material_utilization(), 16 / 21, places=5)

    def test_work_center_utilization_and_costs(self):
        u = self.p.analytics.work_center_utilization(MON, MON + timedelta(days=6))  # 5 working days * 8h
        self.assertAlmostEqual(u["CUT"], 8 / 40)
        self.assertEqual(u["ASM"], 0.0)
        costs = self.p.analytics.production_costs()["TOP-1"]
        self.assertEqual(costs["total"], 210 + 8 * 35)
        self.assertAlmostEqual(costs["cost_per_unit"], 490 / 8)
        self.assertIn("efficiency", self.p.analytics.summary(MON, MON))

    def test_standard_vs_actual(self):
        v = self.p.costing.standard_vs_actual(self.o.id)
        self.assertAlmostEqual(v["variance"], v["actual"] - v["standard"])

    def test_audit_trail(self):
        a = self.p.audit
        self.assertTrue(a.verify())
        hist = a.history("production_order", self.o.id)
        self.assertEqual([e.action for e in hist][:4], ["create", "release", "start", "consume"])
        self.assertTrue(all(isinstance(e, AuditEntry) and e.actor == "tester" for e in hist))
        self.assertEqual(a.history("material", "PLY", "receive")[0].after["on_hand"], 100)
        self.assertEqual(hist[1].before["status"], "PLANNED")

    def test_audit_actor_switch_and_tamper_detection(self):
        with self.p.as_user("bob"):
            self.p.inventory.receive("PAINT", 1)
        self.assertEqual(self.p.audit.history("material", "PAINT", "receive")[-1].actor, "bob")
        self.assertTrue(self.p.audit.verify())
        self.p.audit._entries[3] = self.p.audit._entries[3].__class__(
            **{**self.p.audit._entries[3].__dict__, "actor": "mallory"})
        self.assertFalse(self.p.audit.verify())

    def test_failed_operations_are_not_audited(self):
        n = len(self.p.audit)
        with self.assertRaises(Exception):
            self.p.inventory.receive("PLY", -1)
        self.assertEqual(len(self.p.audit), n)


if __name__ == "__main__":
    unittest.main()
