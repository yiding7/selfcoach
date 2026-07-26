"""手写 SVG 图表原语。零依赖。

为什么不用 matplotlib：这个项目承诺 clone 下来就能跑，不需要 pip install。
而且图表要内联进 HTML 单文件，matplotlib 出 PNG 反而多一层。

**颜色一律不写死。** 每个图元只带 class，填色引用 CSS 变量（var(--s1) 这种）。
因为 SVG 是内联在 HTML 里的，它会继承页面样式表 —— 于是同一份 SVG
在浅色和深色模式下自动变色，不需要生成两份，也不需要一行 JS。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


def esc(text) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def nice_ticks(lo: float, hi: float, count: int = 5) -> list[float]:
    """1/2/5×10ⁿ 的刻度。"""
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / max(count, 1)
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    for mult in (1, 2, 2.5, 5, 10):
        step = mag * mult
        if step >= raw:
            break
    start = math.floor(lo / step) * step
    ticks, v = [], start
    while v <= hi + step * 0.001:
        if v >= lo - step * 0.001:
            ticks.append(round(v, 10))
        v += step
    return ticks or [lo, hi]


def fmt_num(v: float) -> str:
    if v is None:
        return "—"
    if abs(v) >= 10000:
        return f"{v / 1000:.1f}k"
    if abs(v) >= 100:
        return f"{v:.0f}"
    if abs(v) >= 10:
        return f"{v:.1f}".rstrip("0").rstrip(".")
    return f"{v:.2f}".rstrip("0").rstrip(".")


@dataclass
class Series:
    name: str
    points: list[tuple[float, float]]      # (x, y)，x 是索引或时间戳
    css: str = "s1"
    dashed: bool = False
    show_dots: bool = True


@dataclass
class Chart:
    width: int = 720
    height: int = 260
    pad_left: int = 46
    pad_right: int = 14
    pad_top: int = 16
    pad_bottom: int = 34
    parts: list[str] = field(default_factory=list)

    @property
    def plot_w(self) -> int:
        return self.width - self.pad_left - self.pad_right

    @property
    def plot_h(self) -> int:
        return self.height - self.pad_top - self.pad_bottom

    def open(self, title: str = "") -> str:
        return (f'<svg class="chart" viewBox="0 0 {self.width} {self.height}" '
                f'preserveAspectRatio="xMidYMid meet" role="img" '
                f'aria-label="{esc(title)}">')

    def render(self, title: str = "") -> str:
        return self.open(title) + "".join(self.parts) + "</svg>"


def _axes(c: Chart, y_ticks: list[float], x_labels: list[str],
          y_min: float, y_span: float, *, y_unit: str = "") -> list[str]:
    out = []
    for t in y_ticks:
        y = c.pad_top + c.plot_h - (t - y_min) / y_span * c.plot_h
        out.append(f'<line class="grid" x1="{c.pad_left}" y1="{y:.1f}" '
                   f'x2="{c.width - c.pad_right}" y2="{y:.1f}"/>')
        out.append(f'<text class="tick" x="{c.pad_left - 6}" y="{y + 3.5:.1f}" '
                   f'text-anchor="end">{esc(fmt_num(t))}{esc(y_unit)}</text>')
    n = len(x_labels)
    if n:
        step = max(1, n // 8)
        for i, label in enumerate(x_labels):
            if i % step and i != n - 1:
                continue
            x = c.pad_left + (i / max(n - 1, 1)) * c.plot_w
            out.append(f'<text class="tick" x="{x:.1f}" '
                       f'y="{c.height - c.pad_bottom + 15}" '
                       f'text-anchor="middle">{esc(label)}</text>')
    return out


def line_chart(series: list[Series], x_labels: list[str], *, width: int = 720,
               height: int = 260, y_unit: str = "", zero_base: bool = False,
               bands: list[tuple[float, float, str, str]] | None = None,
               title: str = "") -> str:
    """折线图。bands = [(y1, y2, css_class, 说明)]，用来画 MEV/MAV 这种参考带。"""
    c = Chart(width=width, height=height)
    all_y = [y for s in series for _, y in s.points if y is not None]
    if bands:
        all_y += [v for b in bands for v in b[:2]]
    if not all_y:
        return f'<svg class="chart" viewBox="0 0 {width} {height}"></svg>'

    y_lo = 0.0 if zero_base else min(all_y)
    y_hi = max(all_y)
    if y_hi == y_lo:
        y_hi = y_lo + 1
    margin = (y_hi - y_lo) * 0.12
    y_lo = 0.0 if zero_base else y_lo - margin
    y_hi += margin
    ticks = nice_ticks(y_lo, y_hi)
    y_lo, y_hi = min(y_lo, ticks[0]), max(y_hi, ticks[-1])
    span = y_hi - y_lo

    def sx(x: float, n: int) -> float:
        return c.pad_left + (x / max(n - 1, 1)) * c.plot_w

    def sy(y: float) -> float:
        return c.pad_top + c.plot_h - (y - y_lo) / span * c.plot_h

    for lo, hi, css, label in (bands or []):
        y1, y2 = sy(hi), sy(lo)
        c.parts.append(f'<rect class="band {css}" x="{c.pad_left}" y="{y1:.1f}" '
                       f'width="{c.plot_w}" height="{max(y2 - y1, 0):.1f}"><title>'
                       f'{esc(label)}</title></rect>')

    c.parts += _axes(c, ticks, x_labels, y_lo, span, y_unit=y_unit)

    for s in series:
        pts = [(sx(x, len(x_labels)), sy(y)) for x, y in s.points if y is not None]
        if not pts:
            continue
        d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        dash = ' stroke-dasharray="5 4"' if s.dashed else ""
        c.parts.append(f'<path class="line {s.css}" d="{d}"{dash}/>')
        if s.show_dots:
            for x, y in pts:
                c.parts.append(f'<circle class="dot {s.css}" cx="{x:.1f}" '
                               f'cy="{y:.1f}" r="2.6"/>')
    return c.render(title)


def scatter_overlay(points: list[tuple[float, float]], x_labels: list[str],
                    y_lo: float, y_hi: float, *, css: str = "raw",
                    width: int = 720, height: int = 260) -> str:
    """给折线图叠加原始散点（比如体重的每日读数）。单独函数便于复用坐标系。"""
    c = Chart(width=width, height=height)
    span = (y_hi - y_lo) or 1
    out = []
    for x, y in points:
        px = c.pad_left + (x / max(len(x_labels) - 1, 1)) * c.plot_w
        py = c.pad_top + c.plot_h - (y - y_lo) / span * c.plot_h
        out.append(f'<circle class="dot {css}" cx="{px:.1f}" cy="{py:.1f}" r="2"/>')
    return "".join(out)


def bar_chart(labels: list[str], values: list[float | None], *, width: int = 720,
              height: int = 240, y_unit: str = "", css_by_index=None,
              bands: list[tuple[float, float, str, str]] | None = None,
              per_bar_bands: list[tuple[float, float, str] | None] | None = None,
              title: str = "") -> str:
    """柱状图。

    per_bar_bands[i] = (lo, hi, 说明)，在第 i 根柱子背后画它**自己的**目标区间。
    这个参数存在的理由：各肌群的 MEV/MAV/MRV 不一样（胸 8/12-20/22，腹部 4/8-16/20），
    画一条横贯全图的参考带会让人拿胸的标准去看腹部，得出错误判断。
    """
    c = Chart(width=width, height=height, pad_bottom=42)
    known = [v for v in values if v is not None]
    band_max = max((b[1] for b in bands), default=0) if bands else 0
    if per_bar_bands:
        band_max = max([band_max] + [b[1] for b in per_bar_bands if b])
    y_hi = max(known + [band_max]) if (known or band_max) else 1
    ticks = nice_ticks(0, y_hi)
    y_hi = max(y_hi, ticks[-1])

    def sy(y: float) -> float:
        return c.pad_top + c.plot_h - (y / y_hi) * c.plot_h

    for lo, hi, css, label in (bands or []):
        y1, y2 = sy(hi), sy(lo)
        c.parts.append(f'<rect class="band {css}" x="{c.pad_left}" y="{y1:.1f}" '
                       f'width="{c.plot_w}" height="{max(y2 - y1, 0):.1f}">'
                       f'<title>{esc(label)}</title></rect>')

    if per_bar_bands:
        n_ = max(len(labels), 1)
        slot_ = c.plot_w / n_
        bw_ = min(slot_ * 0.78, 58)
        for i, band in enumerate(per_bar_bands):
            if not band:
                continue
            lo, hi, label = band
            cx = c.pad_left + slot_ * (i + 0.5)
            y1, y2 = sy(hi), sy(lo)
            c.parts.append(f'<rect class="band ok" x="{cx - bw_ / 2:.1f}" '
                           f'y="{y1:.1f}" width="{bw_:.1f}" '
                           f'height="{max(y2 - y1, 0):.1f}" rx="2">'
                           f'<title>{esc(label)}</title></rect>')

    for t in ticks:
        y = sy(t)
        c.parts.append(f'<line class="grid" x1="{c.pad_left}" y1="{y:.1f}" '
                       f'x2="{c.width - c.pad_right}" y2="{y:.1f}"/>')
        c.parts.append(f'<text class="tick" x="{c.pad_left - 6}" y="{y + 3.5:.1f}" '
                       f'text-anchor="end">{esc(fmt_num(t))}</text>')

    n = max(len(labels), 1)
    slot = c.plot_w / n
    bw = min(slot * 0.62, 46)
    for i, (label, v) in enumerate(zip(labels, values)):
        cx = c.pad_left + slot * (i + 0.5)
        css = css_by_index(i) if css_by_index else "s1"
        if v is not None:
            y = sy(v)
            c.parts.append(f'<rect class="bar {css}" x="{cx - bw / 2:.1f}" y="{y:.1f}" '
                           f'width="{bw:.1f}" height="{max(c.pad_top + c.plot_h - y, 0):.1f}" '
                           f'rx="2"><title>{esc(label)}: {esc(fmt_num(v))}{esc(y_unit)}'
                           f'</title></rect>')
            c.parts.append(f'<text class="barval" x="{cx:.1f}" y="{y - 4:.1f}" '
                           f'text-anchor="middle">{esc(fmt_num(v))}</text>')
        c.parts.append(f'<text class="tick" x="{cx:.1f}" '
                       f'y="{c.height - c.pad_bottom + 15}" '
                       f'text-anchor="middle">{esc(label)}</text>')
    return c.render(title)


def stacked_bar(labels: list[str], stacks: list[dict[str, float]],
                keys: list[str], css_for, *, width: int = 720, height: int = 260,
                y_unit: str = "", title: str = "") -> str:
    """堆叠柱状图。stacks[i] 是第 i 根柱子上 key → 数值。"""
    c = Chart(width=width, height=height, pad_bottom=42)
    totals = [sum(s.get(k, 0) for k in keys) for s in stacks]
    y_hi = max(totals) if totals else 1
    if y_hi <= 0:
        y_hi = 1
    ticks = nice_ticks(0, y_hi)
    y_hi = max(y_hi, ticks[-1])

    def sy(y: float) -> float:
        return c.pad_top + c.plot_h - (y / y_hi) * c.plot_h

    for t in ticks:
        y = sy(t)
        c.parts.append(f'<line class="grid" x1="{c.pad_left}" y1="{y:.1f}" '
                       f'x2="{c.width - c.pad_right}" y2="{y:.1f}"/>')
        c.parts.append(f'<text class="tick" x="{c.pad_left - 6}" y="{y + 3.5:.1f}" '
                       f'text-anchor="end">{esc(fmt_num(t))}</text>')

    n = max(len(labels), 1)
    slot = c.plot_w / n
    bw = min(slot * 0.62, 46)
    for i, (label, stack) in enumerate(zip(labels, stacks)):
        cx = c.pad_left + slot * (i + 0.5)
        acc = 0.0
        for k in keys:
            v = stack.get(k, 0)
            if not v:
                continue
            y_top, y_bot = sy(acc + v), sy(acc)
            c.parts.append(f'<rect class="bar {css_for(k)}" x="{cx - bw / 2:.1f}" '
                           f'y="{y_top:.1f}" width="{bw:.1f}" '
                           f'height="{max(y_bot - y_top, 0):.1f}">'
                           f'<title>{esc(label)} · {esc(k)}: {esc(fmt_num(v))}'
                           f'{esc(y_unit)}</title></rect>')
            acc += v
        c.parts.append(f'<text class="tick" x="{cx:.1f}" '
                       f'y="{c.height - c.pad_bottom + 15}" '
                       f'text-anchor="middle">{esc(label)}</text>')
    return c.render(title)


def sparkline(values: list[float], *, width: int = 96, height: int = 24,
              css: str = "s1") -> str:
    known = [v for v in values if v is not None]
    if len(known) < 2:
        return f'<svg class="spark" viewBox="0 0 {width} {height}"></svg>'
    lo, hi = min(known), max(known)
    span = (hi - lo) or 1
    pad = 3
    pts = []
    for i, v in enumerate(values):
        if v is None:
            continue
        x = pad + i / max(len(values) - 1, 1) * (width - 2 * pad)
        y = height - pad - (v - lo) / span * (height - 2 * pad)
        pts.append(f"{x:.1f},{y:.1f}")
    last_up = known[-1] >= known[0]
    trend = "up" if last_up else "down"
    return (f'<svg class="spark" viewBox="0 0 {width} {height}">'
            f'<polyline class="line {css} {trend}" points="{" ".join(pts)}"/></svg>')


def donut(parts: list[tuple[str, float]], css_for, *, size: int = 150,
          center_label: str = "", center_sub: str = "") -> str:
    total = sum(v for _, v in parts if v > 0)
    if total <= 0:
        return f'<svg class="donut" viewBox="0 0 {size} {size}"></svg>'
    r, cx, cy, thick = size * 0.38, size / 2, size / 2, size * 0.16
    out, angle = [], -math.pi / 2
    for name, v in parts:
        if v <= 0:
            continue
        sweep = v / total * math.tau
        x1, y1 = cx + r * math.cos(angle), cy + r * math.sin(angle)
        angle += sweep
        x2, y2 = cx + r * math.cos(angle), cy + r * math.sin(angle)
        large = 1 if sweep > math.pi else 0
        out.append(f'<path class="arc {css_for(name)}" fill="none" '
                   f'stroke-width="{thick:.1f}" d="M{x1:.2f},{y1:.2f} '
                   f'A{r:.2f},{r:.2f} 0 {large} 1 {x2:.2f},{y2:.2f}">'
                   f'<title>{esc(name)}: {esc(fmt_num(v))}</title></path>')
    if center_label:
        out.append(f'<text class="donut-main" x="{cx}" y="{cy - 1}" '
                   f'text-anchor="middle">{esc(center_label)}</text>')
    if center_sub:
        out.append(f'<text class="donut-sub" x="{cx}" y="{cy + 14}" '
                   f'text-anchor="middle">{esc(center_sub)}</text>')
    return (f'<svg class="donut" viewBox="0 0 {size} {size}">'
            + "".join(out) + "</svg>")


def calendar_heatmap(day_values: dict[str, float], start: str, end: str, *,
                     cell: int = 12, gap: int = 3, buckets: int = 5) -> str:
    """GitHub 风格的日历热力图。key 是 YYYY-MM-DD。"""
    import datetime as dt

    d0, d1 = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    # 从所在周的周一开始，列才能对齐
    d0 -= dt.timedelta(days=d0.weekday())
    weeks = ((d1 - d0).days // 7) + 1
    width = weeks * (cell + gap) + 34
    height = 7 * (cell + gap) + 22

    known = [v for v in day_values.values() if v]
    vmax = max(known) if known else 1

    out = []
    for i, wd in enumerate(["一", "", "三", "", "五", "", "日"]):
        if wd:
            out.append(f'<text class="tick" x="18" '
                       f'y="{18 + i * (cell + gap) + cell - 2}" '
                       f'text-anchor="end">{wd}</text>')

    seen_months = set()
    d = d0
    while d <= d1:
        col = (d - d0).days // 7
        row = d.weekday()
        x = 26 + col * (cell + gap)
        y = 14 + row * (cell + gap)
        v = day_values.get(d.isoformat())
        if v:
            level = min(buckets, max(1, math.ceil(v / vmax * buckets)))
            cls = f"heat-{level}"
            tip = f"{d.isoformat()}: {fmt_num(v)}"
        else:
            cls = "heat-0"
            tip = f"{d.isoformat()}: 无训练"
        out.append(f'<rect class="cell {cls}" x="{x}" y="{y}" width="{cell}" '
                   f'height="{cell}" rx="2"><title>{esc(tip)}</title></rect>')
        if d.day <= 7 and d.month not in seen_months:
            seen_months.add(d.month)
            out.append(f'<text class="tick" x="{x}" y="10">{d.month}月</text>')
        d += dt.timedelta(days=1)

    return (f'<svg class="chart calendar" viewBox="0 0 {width} {height}" '
            f'preserveAspectRatio="xMinYMin meet">' + "".join(out) + "</svg>")
