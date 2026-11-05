from datetime import date

from mfg import Component, Operation, Plant

MON = date(2026, 11, 2)  # a Monday


def build_plant(stock=True) -> Plant:
    """DESK = 1 TOP (sub-assembly) + 8 SCREW + 0.5 PAINT (10% scrap); TOP = 2 PLY (5% scrap)."""
    p = Plant(today=lambda: MON, actor="tester")
    p.products.add_category("Furniture")
    p.products.add_category("Desks", "Furniture")
    p.products.create_product("desk-1", "Office Desk", "Desks", specs={"color": "oak"})
    p.products.create_product("TOP-1", "Desk Top", "Furniture")
    p.inventory.add_supplier("S1", "Timber Co", "t@x.test", lead_time_days=5)
    p.inventory.add_supplier("S2", "Hardware Ltd", lead_time_days=2)
    p.inventory.create_material("PLY", "Plywood", "sheet", "S1", 10.0, reorder_level=20, reorder_qty=50)
    p.inventory.create_material("SCREW", "Screw", "pc", "S2", 0.1, reorder_level=100, reorder_qty=500)
    p.inventory.create_material("PAINT", "Paint", "L", "S2", 8.0, reorder_level=2, reorder_qty=10)
    if stock:
        p.inventory.receive("PLY", 100)
        p.inventory.receive("SCREW", 2000)
        p.inventory.receive("PAINT", 30)
    p.work_centers.create("CUT", "Saw", 8, 100, machine_rate=20, labor_rate=15)
    p.work_centers.create("ASM", "Assembly", 8, 50, machine_rate=5, labor_rate=25)
    p.boms.create_version("TOP-1", [Component("material", "PLY", 2, 5)], [Operation(10, "CUT", 60, 30)])
    p.boms.activate("TOP-1", 1)
    p.boms.create_version("DESK-1", [Component("product", "TOP-1", 1), Component("material", "SCREW", 8),
                                     Component("material", "PAINT", 0.5, 10)],
                          [Operation(10, "ASM", 30, 60)])
    p.boms.activate("DESK-1", 1)
    return p
