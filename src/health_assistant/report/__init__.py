"""报表引擎：确定性事实 → facts.json → 自包含 HTML。"""

from .build import build, month_bounds, slug, week_bounds, year_bounds
from .render import render

__all__ = ["build", "render", "slug", "week_bounds", "month_bounds", "year_bounds"]
