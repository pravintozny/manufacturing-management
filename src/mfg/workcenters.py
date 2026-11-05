"""Work centers / machines: capacity, availability, calendars."""
from __future__ import annotations

import copy
from datetime import date, timedelta
from typing import Dict, List

from .context import Context
from .errors import DuplicateError, ValidationError
from .models import WorkCenter


class WorkCenterService:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        # work center -> day -> scheduled hours
        self.load: Dict[str, Dict[date, float]] = {}

    def create(self, code: str, name: str, hours_per_day: float, availability_pct: float = 100.0,
               machine_rate: float = 0.0, labor_rate: float = 0.0) -> WorkCenter:
        code = code.strip().upper()
        if code in self.ctx.work_centers:
            raise DuplicateError(f"work center '{code}' exists")
        self._validate(hours_per_day, availability_pct, machine_rate, labor_rate)
        wc = WorkCenter(code, name, hours_per_day, availability_pct, machine_rate, labor_rate)
        self.ctx.work_centers[code] = wc
        self.ctx.log("create", "work_center", code, after=wc)
        return wc

    @staticmethod
    def _validate(hours, avail, mrate, lrate):
        if not 0 < hours <= 24:
            raise ValidationError("hours_per_day must be in (0, 24]")
        if not 0 <= avail <= 100:
            raise ValidationError("availability_pct must be in [0, 100]")
        if mrate < 0 or lrate < 0:
            raise ValidationError("rates must be >= 0")

    def get(self, code: str) -> WorkCenter:
        return self.ctx.get(self.ctx.work_centers, code.upper(), "work center")

    def update(self, code: str, *, hours_per_day=None, availability_pct=None,
               machine_rate=None, labor_rate=None) -> WorkCenter:
        wc = self.get(code)
        before = copy.deepcopy(wc)
        h = wc.hours_per_day if hours_per_day is None else hours_per_day
        a = wc.availability_pct if availability_pct is None else availability_pct
        m = wc.machine_rate if machine_rate is None else machine_rate
        l = wc.labor_rate if labor_rate is None else labor_rate
        self._validate(h, a, m, l)
        wc.hours_per_day, wc.availability_pct, wc.machine_rate, wc.labor_rate = h, a, m, l
        self.ctx.log("update", "work_center", wc.code, before, wc)
        return wc

    def add_downtime(self, code: str, day: date) -> None:
        wc = self.get(code)
        if day not in wc.downtime:
            before = copy.deepcopy(wc)
            wc.downtime.append(day)
            self.ctx.log("downtime", "work_center", wc.code, before, wc)

    def capacity(self, code: str, day: date) -> float:
        """Effective productive hours on a given day."""
        wc = self.get(code)
        if day.weekday() not in wc.working_weekdays or day in wc.downtime:
            return 0.0
        return wc.hours_per_day * wc.availability_pct / 100.0

    def available_hours(self, code: str, start: date, end: date) -> float:
        days = (end - start).days + 1
        return sum(self.capacity(code, start + timedelta(d)) for d in range(max(days, 0)))

    def scheduled_load(self, code: str, start: date, end: date) -> float:
        return sum(h for d, h in self.load.get(code.upper(), {}).items() if start <= d <= end)

    def free_capacity(self, code: str, day: date) -> float:
        return max(0.0, self.capacity(code, day) - self.load.get(code.upper(), {}).get(day, 0.0))

    def book(self, code: str, day: date, hours: float):
        d = self.load.setdefault(code.upper(), {})
        d[day] = d.get(day, 0.0) + hours

    def unbook(self, code: str, day: date, hours: float):
        d = self.load.setdefault(code.upper(), {})
        d[day] = max(0.0, d.get(day, 0.0) - hours)

    def list(self) -> List[WorkCenter]:
        return sorted(self.ctx.work_centers.values(), key=lambda w: w.code)
