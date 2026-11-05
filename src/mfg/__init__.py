"""Manufacturing and production planning system."""
from datetime import date
from typing import Callable

from .analytics import AnalyticsService
from .audit import AuditLog
from .bom import BomService
from .catalog import ProductService
from .context import Context
from .costing import CostService
from .execution import ExecutionService
from .inventory import InventoryService
from .models import Component, Operation
from .mrp import MrpService
from .planning import PlanningService
from .quality import QualityService
from .workcenters import WorkCenterService

__all__ = ["Plant", "Component", "Operation"]


class Plant:
    """Facade wiring all services over one shared Context."""

    def __init__(self, today: Callable[[], date] = date.today, actor: str = "system"):
        self.ctx = Context(today, actor)
        self.products = ProductService(self.ctx)
        self.inventory = InventoryService(self.ctx)
        self.work_centers = WorkCenterService(self.ctx)
        self.boms = BomService(self.ctx)
        self.planning = PlanningService(self.ctx, self.boms, self.work_centers)
        self.mrp = MrpService(self.ctx, self.planning)
        self.quality = QualityService(self.ctx, self.planning)
        self.execution = ExecutionService(self.ctx, self.planning, self.boms, self.quality)
        self.costing = CostService(self.ctx, self.planning, self.boms)
        self.analytics = AnalyticsService(self.ctx, self.boms, self.work_centers)

    @property
    def audit(self) -> AuditLog:
        return self.ctx.audit

    def as_user(self, actor: str):
        return self.ctx.as_user(actor)
