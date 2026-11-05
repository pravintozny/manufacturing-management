"""Product management: categories, finished products, specifications, SKUs."""
from __future__ import annotations

import copy
import re
from typing import List, Optional

from .context import Context
from .errors import DuplicateError, StateError, ValidationError
from .models import OPEN_STATUSES, Category, Product

SKU_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{1,31}$")


class ProductService:
    def __init__(self, ctx: Context):
        self.ctx = ctx

    def add_category(self, name: str, parent: Optional[str] = None) -> Category:
        name = name.strip()
        if not name:
            raise ValidationError("category name required")
        if name in self.ctx.categories:
            raise DuplicateError(f"category '{name}' exists")
        if parent is not None:
            self.ctx.get(self.ctx.categories, parent, "category")
        cat = Category(name, parent)
        self.ctx.categories[name] = cat
        self.ctx.log("create", "category", name, after=cat)
        return cat

    def category_tree(self, name: str) -> List[str]:
        """The category and all its descendants."""
        self.ctx.get(self.ctx.categories, name, "category")
        out, frontier = [name], [name]
        while frontier:
            cur = frontier.pop()
            for c in self.ctx.categories.values():
                if c.parent == cur:
                    out.append(c.name)
                    frontier.append(c.name)
        return out

    def create_product(self, sku: str, name: str, category: str, unit: str = "EA",
                       specs: Optional[dict] = None) -> Product:
        sku = sku.strip().upper()
        if not SKU_RE.match(sku):
            raise ValidationError(f"invalid SKU '{sku}' (A-Z, 0-9, . _ -, 2-32 chars)")
        if sku in self.ctx.products:
            raise DuplicateError(f"SKU '{sku}' exists")
        if not name.strip():
            raise ValidationError("product name required")
        self.ctx.get(self.ctx.categories, category, "category")
        p = Product(sku, name.strip(), category, unit, {str(k): str(v) for k, v in (specs or {}).items()})
        self.ctx.products[sku] = p
        self.ctx.log("create", "product", sku, after=p)
        return p

    def get(self, sku: str) -> Product:
        return self.ctx.get(self.ctx.products, sku.upper(), "product")

    def update_product(self, sku: str, *, name=None, category=None, specs=None, active=None) -> Product:
        p = self.get(sku)
        before = copy.deepcopy(p)
        if name is not None:
            if not name.strip():
                raise ValidationError("product name required")
            p.name = name.strip()
        if category is not None:
            self.ctx.get(self.ctx.categories, category, "category")
            p.category = category
        if specs is not None:
            p.specs = {**p.specs, **{str(k): str(v) for k, v in specs.items()}}
        if active is not None:
            if not active and any(o.sku == p.sku and o.status in OPEN_STATUSES
                                  for o in self.ctx.orders.values()):
                raise StateError(f"{p.sku} has open production orders")
            p.active = active
        self.ctx.log("update", "product", p.sku, before, p)
        return p

    def list(self, category: Optional[str] = None, active_only: bool = False) -> List[Product]:
        cats = set(self.category_tree(category)) if category else None
        return sorted((p for p in self.ctx.products.values()
                       if (cats is None or p.category in cats) and (p.active or not active_only)),
                      key=lambda p: p.sku)
