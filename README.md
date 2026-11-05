# manufacturing-management

Manufacturing and production planning system (pure Python 3.9+, standard library only, in-memory state).

```python
from datetime import date
from mfg import Plant, Component, Operation

plant = Plant(actor="planner")
plant.products.add_category("Furniture")
plant.products.create_product("DESK-1", "Desk", "Furniture")
plant.inventory.create_material("PLY", "Plywood", "sheet", None, 10.0, reorder_level=20, reorder_qty=50)
plant.inventory.receive("PLY", 100)
plant.work_centers.create("ASM", "Assembly", 8, 90, machine_rate=5, labor_rate=25)
plant.boms.create_version("DESK-1", [Component("material", "PLY", 2, 5)], [Operation(10, "ASM", 30, 60)])
plant.boms.activate("DESK-1", 1)
order = plant.planning.create_order("DESK-1", 10, due_date=date(2026, 12, 1))
print(plant.mrp.run())
```

## Run the tests
```
PYTHONPATH=src:tests python -m unittest discover -s tests -v
```

Architecture: see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
