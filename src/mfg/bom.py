"""Bill of Materials: versions, product-to-material links, dependency-aware explosion."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .context import EPS, Context, q
from .errors import DependencyError, NotFoundError, StateError, ValidationError
from .models import ACTIVE, DRAFT, OBSOLETE, BomVersion, Component, Operation


@dataclass
class Node:
    """One production-order-worth of demand in the exploded BOM tree."""
    sku: str
    qty: float
    version: int
    materials: Dict[str, float] = field(default_factory=dict)      # raw materials (incl. scrap)
    product_needs: Dict[str, float] = field(default_factory=dict)  # sub-assemblies required
    pulls: Dict[str, float] = field(default_factory=dict)          # part covered by stock
    children: List["Node"] = field(default_factory=list)           # sub-assemblies to make

    def total_materials(self) -> Dict[str, float]:
        out = dict(self.materials)
        for c in self.children:
            for k, v in c.total_materials().items():
                out[k] = q(out.get(k, 0.0) + v)
        return out


class BomService:
    def __init__(self, ctx: Context):
        self.ctx = ctx

    # versions --------------------------------------------------------------
    def create_version(self, sku: str, components: List[Component], operations: List[Operation],
                       note: str = "") -> BomVersion:
        sku = sku.upper()
        self.ctx.get(self.ctx.products, sku, "product")
        if not components:
            raise ValidationError("BOM needs at least one component")
        seen = set()
        for c in components:
            if c.kind not in ("material", "product"):
                raise ValidationError(f"bad component kind '{c.kind}'")
            c.ref = c.ref.upper()
            if (c.kind, c.ref) in seen:
                raise ValidationError(f"duplicate component {c.ref}")
            seen.add((c.kind, c.ref))
            if c.qty_per_unit <= 0:
                raise ValidationError(f"{c.ref}: qty_per_unit must be > 0")
            if not 0 <= c.scrap_pct < 100:
                raise ValidationError(f"{c.ref}: scrap_pct must be in [0, 100)")
            if c.kind == "material":
                self.ctx.get(self.ctx.materials, c.ref, "material")
            else:
                if c.ref == sku:
                    raise DependencyError(f"{sku} cannot contain itself")
                self.ctx.get(self.ctx.products, c.ref, "product")
        seqs = [o.seq for o in operations]
        if len(set(seqs)) != len(seqs):
            raise ValidationError("duplicate operation seq")
        for o in operations:
            self.ctx.get(self.ctx.work_centers, o.work_center.upper(), "work center")
            o.work_center = o.work_center.upper()
            if o.setup_minutes < 0 or o.minutes_per_unit < 0:
                raise ValidationError("operation times must be >= 0")
        versions = self.ctx.boms.setdefault(sku, [])
        bom = BomVersion(sku, len(versions) + 1, list(components), sorted(operations, key=lambda o: o.seq), DRAFT, note)
        versions.append(bom)
        self.ctx.log("create", "bom", f"{sku}@v{bom.version}", after=bom)
        return bom

    def get(self, sku: str, version: Optional[int] = None) -> BomVersion:
        sku = sku.upper()
        versions = self.ctx.boms.get(sku, [])
        if version is None:
            return self.active(sku)
        if not 1 <= version <= len(versions):
            raise NotFoundError(f"BOM {sku} v{version} not found")
        return versions[version - 1]

    def active(self, sku: str) -> BomVersion:
        for b in self.ctx.boms.get(sku.upper(), []):
            if b.status == ACTIVE:
                return b
        raise DependencyError(f"{sku.upper()} has no active BOM")

    def activate(self, sku: str, version: int) -> BomVersion:
        bom = self.get(sku, version)
        if bom.status == ACTIVE:
            return bom
        if bom.status == OBSOLETE:
            raise StateError("obsolete BOM versions cannot be reactivated")
        self._check_cycle(bom)
        for other in self.ctx.boms[bom.sku]:
            if other.status == ACTIVE:
                other.status = OBSOLETE
                self.ctx.log("obsolete", "bom", f"{other.sku}@v{other.version}", after=other.status)
        bom.status = ACTIVE
        self.ctx.log("activate", "bom", f"{bom.sku}@v{bom.version}", after=bom.status)
        return bom

    def versions(self, sku: str) -> List[BomVersion]:
        return list(self.ctx.boms.get(sku.upper(), []))

    def where_used(self, ref: str) -> List[str]:
        """SKUs whose ACTIVE BOM uses the given material code or product SKU."""
        ref = ref.upper()
        return sorted(sku for sku, vs in self.ctx.boms.items()
                      for b in vs if b.status == ACTIVE and any(c.ref == ref for c in b.components))

    # dependency handling ---------------------------------------------------
    def _check_cycle(self, candidate: BomVersion):
        def children(sku: str):
            bom = candidate if sku == candidate.sku else next(
                (b for b in self.ctx.boms.get(sku, []) if b.status == ACTIVE), None)
            return [c.ref for c in bom.components if c.kind == "product"] if bom else []

        def visit(sku, path):
            if sku in path:
                raise DependencyError("BOM cycle: " + " -> ".join(path + [sku]))
            for ch in children(sku):
                visit(ch, path + [sku])
        visit(candidate.sku, [])

    def build_tree(self, sku: str, qty: float, avail_products: Dict[str, float],
                   version: Optional[int] = None, _path: tuple = ()) -> Node:
        """Explode `qty` of `sku`. Sub-assemblies are taken from `avail_products`
        stock first (mutated); only the shortfall becomes child nodes to be made."""
        sku = sku.upper()
        if sku in _path:
            raise DependencyError("BOM cycle: " + " -> ".join(_path + (sku,)))
        bom = self.get(sku, version)
        node = Node(sku, q(qty), bom.version)
        for c in bom.components:
            need = q(qty * c.qty_per_unit * (1 + c.scrap_pct / 100.0))
            if c.kind == "material":
                node.materials[c.ref] = q(node.materials.get(c.ref, 0.0) + need)
                continue
            node.product_needs[c.ref] = need
            take = min(max(avail_products.get(c.ref, 0.0), 0.0), need)
            avail_products[c.ref] = q(avail_products.get(c.ref, 0.0) - take)
            if take > EPS:
                node.pulls[c.ref] = q(take)
            if need - take > EPS:
                node.children.append(self.build_tree(c.ref, need - take, avail_products,
                                                     None, _path + (sku,)))
        return node

    # standard (planned) cost -------------------------------------------------
    def standard_cost(self, sku: str, lot_size: float = 1.0, version: Optional[int] = None,
                      _path: tuple = ()) -> Dict[str, float]:
        """Per-unit planned cost: material (incl. scrap), labor, machine."""
        sku = sku.upper()
        if sku in _path:
            raise DependencyError("BOM cycle: " + " -> ".join(_path + (sku,)))
        if lot_size <= 0:
            raise ValidationError("lot_size must be > 0")
        bom = self.get(sku, version)
        mat = 0.0
        for c in bom.components:
            f = c.qty_per_unit * (1 + c.scrap_pct / 100.0)
            if c.kind == "material":
                mat += f * self.ctx.materials[c.ref].unit_cost
            else:
                mat += f * self.standard_cost(c.ref, lot_size, None, _path + (sku,))["total"]
        labor = machine = 0.0
        for o in bom.operations:
            wc = self.ctx.work_centers[o.work_center]
            hours = (o.setup_minutes / lot_size + o.minutes_per_unit) / 60.0
            labor += hours * wc.labor_rate
            machine += hours * wc.machine_rate
        return {"material": q(mat), "labor": q(labor), "machine": q(machine),
                "total": q(mat + labor + machine)}
