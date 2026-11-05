import unittest

from helpers import build_plant

from mfg import Component, Operation, Plant
from mfg.errors import (DependencyError, DuplicateError, InsufficientStockError, NotFoundError,
                        StateError, ValidationError)


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.p = build_plant()

    def test_sku_normalised_and_unique(self):
        self.assertEqual(self.p.products.get("desk-1").sku, "DESK-1")
        with self.assertRaises(DuplicateError):
            self.p.products.create_product("DESK-1", "x", "Desks")

    def test_invalid_sku_and_category(self):
        with self.assertRaises(ValidationError):
            self.p.products.create_product("a b", "x", "Desks")
        with self.assertRaises(NotFoundError):
            self.p.products.create_product("OK-1", "x", "Nope")

    def test_category_hierarchy_listing(self):
        skus = [x.sku for x in self.p.products.list(category="Furniture")]
        self.assertEqual(skus, ["DESK-1", "TOP-1"])
        self.assertEqual([x.sku for x in self.p.products.list(category="Desks")], ["DESK-1"])

    def test_specs_merge_and_deactivate_blocked_by_open_order(self):
        self.p.products.update_product("DESK-1", specs={"width": "120cm"})
        self.assertEqual(self.p.products.get("DESK-1").specs, {"color": "oak", "width": "120cm"})
        self.p.planning.create_order("DESK-1", 1, self.p.ctx.today())
        with self.assertRaises(StateError):
            self.p.products.update_product("DESK-1", active=False)


class BomTests(unittest.TestCase):
    def setUp(self):
        self.p = build_plant()

    def test_versioning_obsoletes_previous(self):
        v2 = self.p.boms.create_version("TOP-1", [Component("material", "PLY", 3)], [])
        self.assertEqual(v2.version, 2)
        self.assertEqual(self.p.boms.active("TOP-1").version, 1)
        self.p.boms.activate("TOP-1", 2)
        self.assertEqual(self.p.boms.active("TOP-1").version, 2)
        self.assertEqual(self.p.boms.get("TOP-1", 1).status, "obsolete")
        with self.assertRaises(StateError):
            self.p.boms.activate("TOP-1", 1)

    def test_validation(self):
        b = self.p.boms
        with self.assertRaises(ValidationError):
            b.create_version("TOP-1", [], [])
        with self.assertRaises(ValidationError):
            b.create_version("TOP-1", [Component("material", "PLY", 0)], [])
        with self.assertRaises(ValidationError):
            b.create_version("TOP-1", [Component("material", "PLY", 1, 100)], [])
        with self.assertRaises(NotFoundError):
            b.create_version("TOP-1", [Component("material", "GHOST", 1)], [])
        with self.assertRaises(DependencyError):
            b.create_version("TOP-1", [Component("product", "TOP-1", 1)], [])

    def test_cycle_detected_on_activation(self):
        # TOP-1 -> DESK-1 would close the loop DESK-1 -> TOP-1 -> DESK-1
        v = self.p.boms.create_version("TOP-1", [Component("product", "DESK-1", 1)], [])
        with self.assertRaises(DependencyError):
            self.p.boms.activate("TOP-1", v.version)
        self.assertEqual(self.p.boms.active("TOP-1").version, 1)  # unchanged

    def test_where_used_and_standard_cost(self):
        self.assertEqual(self.p.boms.where_used("ply"), ["TOP-1"])
        self.assertEqual(self.p.boms.where_used("TOP-1"), ["DESK-1"])
        # TOP: 2*1.05*10=21 mat; (60/1 + 30)/60=1.5h*(15+20)=52.5 -> 73.5 at lot 1
        top = self.p.boms.standard_cost("TOP-1")
        self.assertAlmostEqual(top["total"], 73.5)
        # large lot dilutes setup: 0.5h*35 = 17.5 -> 38.5
        self.assertAlmostEqual(self.p.boms.standard_cost("TOP-1", 1e9)["total"], 38.5, places=3)

    def test_no_active_bom(self):
        self.p.products.create_product("NEW-1", "n", "Furniture")
        with self.assertRaises(DependencyError):
            self.p.planning.create_order("NEW-1", 1, self.p.ctx.today())


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.p = build_plant()
        self.inv = self.p.inventory

    def test_weighted_average_cost(self):
        self.inv.receive("PLY", 100, unit_cost=20.0)  # 100@10 + 100@20
        self.assertAlmostEqual(self.inv.get("PLY").unit_cost, 15.0)
        self.assertEqual(self.inv.get("PLY").on_hand, 200)

    def test_rejects_bad_input(self):
        with self.assertRaises(ValidationError):
            self.inv.receive("PLY", 0)
        with self.assertRaises(DuplicateError):
            self.inv.create_material("PLY", "x", "u", None, 1)
        with self.assertRaises(NotFoundError):
            self.inv.create_material("Z", "x", "u", "NOPE", 1)

    def test_adjust_cannot_go_below_reserved(self):
        o = self.p.planning.create_order("TOP-1", 10, self.p.ctx.today())
        self.p.planning.release(o.id)  # reserves 21 PLY
        self.assertEqual(self.inv.available("PLY"), 79)
        with self.assertRaises(InsufficientStockError):
            self.inv.adjust("PLY", -90, "count")
        self.inv.adjust("PLY", -79, "count")
        with self.assertRaises(ValidationError):
            self.inv.adjust("PLY", -1, " ")

    def test_low_stock(self):
        self.inv.adjust("PAINT", -29, "spill")
        self.assertEqual([m.code for m in self.inv.low_stock()], ["PAINT"])


if __name__ == "__main__":
    unittest.main()
