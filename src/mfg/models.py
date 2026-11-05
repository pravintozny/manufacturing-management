"""Plain dataclasses describing the domain. No behaviour lives here."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

# Production order statuses
PLANNED = "PLANNED"
RELEASED = "RELEASED"
IN_PROGRESS = "IN_PROGRESS"
COMPLETED = "COMPLETED"
CANCELLED = "CANCELLED"
OPEN_STATUSES = (PLANNED, RELEASED, IN_PROGRESS)

# BOM version statuses
DRAFT = "draft"
ACTIVE = "active"
OBSOLETE = "obsolete"


@dataclass
class Category:
    name: str
    parent: Optional[str] = None


@dataclass
class Product:
    sku: str
    name: str
    category: str
    unit: str = "EA"
    specs: Dict[str, str] = field(default_factory=dict)
    active: bool = True
    on_hand: float = 0.0      # finished / semi-finished stock
    reserved: float = 0.0
    avg_cost: float = 0.0     # weighted average cost of produced units


@dataclass
class Supplier:
    id: str
    name: str
    contact: str = ""
    lead_time_days: int = 0


@dataclass
class Material:
    code: str
    name: str
    unit: str
    supplier_id: Optional[str]
    unit_cost: float
    on_hand: float = 0.0
    reserved: float = 0.0
    reorder_level: float = 0.0
    reorder_qty: float = 0.0   # purchase lot multiple


@dataclass
class Component:
    kind: str                  # "material" (raw) or "product" (sub-assembly)
    ref: str                   # material code or product SKU
    qty_per_unit: float
    scrap_pct: float = 0.0


@dataclass
class Operation:
    seq: int
    work_center: str
    setup_minutes: float = 0.0
    minutes_per_unit: float = 0.0


@dataclass
class BomVersion:
    sku: str
    version: int
    components: List[Component]
    operations: List[Operation]
    status: str = DRAFT
    note: str = ""


@dataclass
class WorkCenter:
    code: str
    name: str
    hours_per_day: float
    availability_pct: float = 100.0
    machine_rate: float = 0.0   # machine operating cost / hour
    labor_rate: float = 0.0     # labor cost / hour
    working_weekdays: List[int] = field(default_factory=lambda: [0, 1, 2, 3, 4])
    downtime: List[date] = field(default_factory=list)


@dataclass
class ScheduledOp:
    seq: int
    work_center: str
    hours: float
    start: date
    end: date
    slices: Dict[date, float] = field(default_factory=dict)


@dataclass
class ProductionOrder:
    id: str
    sku: str
    qty: float
    due_date: date
    start_date: date
    bom_version: int
    status: str = PLANNED
    parent_id: Optional[str] = None
    children: List[str] = field(default_factory=list)
    schedule: List[ScheduledOp] = field(default_factory=list)
    # plan (direct requirements of this order only)
    material_plan: Dict[str, float] = field(default_factory=dict)
    product_plan: Dict[str, float] = field(default_factory=dict)
    product_pulls: Dict[str, float] = field(default_factory=dict)   # covered by existing stock
    reserved_materials: Dict[str, float] = field(default_factory=dict)
    reserved_products: Dict[str, float] = field(default_factory=dict)
    # execution
    consumed_materials: Dict[str, float] = field(default_factory=dict)
    consumed_products: Dict[str, float] = field(default_factory=dict)
    good: float = 0.0
    rejected: float = 0.0
    defective: float = 0.0       # awaiting disposition
    rework_pending: float = 0.0
    processed: float = 0.0       # cumulative units reported
    setup_done: bool = False
    hours_log: List[tuple] = field(default_factory=list)  # (date, wc, hours)
    material_cost: float = 0.0
    labor_cost: float = 0.0
    machine_cost: float = 0.0
    rework_cost: float = 0.0
    short_closed: bool = False
    unit_cost: Optional[float] = None
    completed_on: Optional[date] = None

    @property
    def planned_end(self) -> Optional[date]:
        return max((o.end for o in self.schedule), default=None)

    @property
    def late(self) -> bool:
        end = self.planned_end
        return end is not None and end > self.due_date


@dataclass
class DefectRecord:
    id: str
    order_id: str
    code: str
    qty: float
    severity: str              # minor | major | critical
    open_qty: float
    source: str                # "production" | "inspection"
    work_center: Optional[str] = None
    dispositions: List[dict] = field(default_factory=list)


@dataclass
class Inspection:
    id: str
    order_id: str
    inspector: str
    inspected_qty: float
    defect_qty: float
    passed: bool
    on: date
    defect_ids: List[str] = field(default_factory=list)
