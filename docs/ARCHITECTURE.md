# Architecture

One shared `Context` (in-memory stores, id generator, clock, audit hook) is injected into small service classes;
`mfg.Plant` wires them together. No third-party dependencies. Persistence is intentionally out of scope: the stores
are plain dicts in `Context`, so a repository layer can replace them without touching business rules.

| Requirement | Module | Notes |
|---|---|---|
| Product management | `catalog.py` | Category tree, normalised unique SKUs, specs, deactivate blocked by open orders |
| BOM | `bom.py` | Versions (draft/active/obsolete, one active), raw-material **and** sub-assembly components, scrap %, routing operations, where-used, standard cost |
| Raw material inventory | `inventory.py` | Suppliers (lead time), weighted-average unit cost on receipt, reorder level/lot, reservations, stock counts |
| Work centers | `workcenters.py` | hours/day x availability %, working weekdays, downtime dates, machine/labor rates, booked load |
| Production planning | `planning.py` | Order trees, finite-capacity scheduling, release (reserve) / cancel |
| MRP | `mrp.py` | Net requirements, shortages, purchase recommendations |
| Execution | `execution.py` | start, consume, report progress, complete / short-close |
| Cost | `costing.py` | Actual material/labor/machine/rework cost, cost per good unit, variance vs standard |
| Quality | `quality.py` | Inspections, defect log, accept/rework/reject dispositions, Pareto |
| Analytics | `analytics.py` | Efficiency, material utilization, costs, defect rates, work center utilization |
| Audit | `audit.py` | Append-only, SHA-256 hash-chained log; `verify()` detects tampering |

## Order lifecycle
`PLANNED -> RELEASED -> IN_PROGRESS -> COMPLETED` (`CANCELLED` from PLANNED/RELEASED only).

## Dependency-aware calculations
* `BomService.build_tree` explodes a BOM recursively. For each sub-assembly it first takes **free finished stock**
  (on hand - reserved); only the shortfall becomes a **child production order**. Quantities include scrap %.
* Cycles are rejected at BOM activation and again during explosion.
* Child orders are scheduled first; the parent starts the day after its last child ends. A parent cannot `start`
  until every child is `COMPLETED`, and cannot start if the sub-assembly stock actually produced is short.
* Sub-assemblies are valued at their produced weighted-average cost when issued to the parent.
* A failed order creation (e.g. no capacity, missing BOM) leaves no partial orders or capacity bookings.

## Stock validation
* Release checks free stock for every material and sub-assembly pull; it is all-or-nothing and reserves on success.
* Consumption draws on the order's own reservation first, then on free stock only (never other orders' reservations);
  stock can never go negative. Consumption over 120% of plan is refused. Manual adjustments cannot go below reserved stock.

## MRP logic
Only `PLANNED` orders create demand (released orders already hold reservations). Orders are served in due-date order
against free stock. Recommendation = `max(shortage, reorder_level - projected_available)` rounded up to the material's
lot size; order-by date = need-by date minus supplier lead time. Materials below reorder level are flagged even with no demand.

## Quality & rework
`report_progress(defective=n)` or `quality.inspect(...)` (moves good units to defective) puts units in a pool that must be
dispositioned: `accept` (not for critical), `rework` -> `complete_rework(fixed, failed, cost)`, or `reject`. An order cannot
complete while defective or rework units are unresolved. Rejected/failed units' costs are absorbed by good units.

## Cost model
Material = quantity consumed x unit cost at consumption; labor/machine = reported hours x work center rates (default: standard
time for units processed, setup once); plus rework cost. Cost per unit = total / good units.

## Analytics definitions
* Efficiency = standard hours earned (units processed x run time + setup) / actual hours.
* Material utilization = good units x BOM qty (no scrap allowance) / actual consumption.
* Defect rate = defect units logged / units processed; first-pass yield = 1 - defect rate.
* Work center utilization = actual hours / available hours (capacity calendar) in a period.

## Known limitations
In-memory only; single currency; no purchase-order receipts (MRP recommends, doesn't track on-order); forward scheduling only;
child-order quantities are fixed at creation time based on stock then.
