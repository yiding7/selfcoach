"""人体肌群热力图。

手绘的简化人体轮廓，正面 + 背面，每个肌群一个独立图形，按本周训练量着色。
颜色一律用 CSS class，不写死 —— 深浅色模式自动跟随。

刻意画得抽象：这是一张「这周练了哪儿」的示意图，不是解剖图。
比起解剖精度，一眼能看懂哪块颜色深更重要。
"""

from __future__ import annotations

from .svg import esc, fmt_num

# 每个肌群在正面/背面视图里的图形。
# 值是 (SVG 图形字符串模板列表, 显示名)。左右对称的部位画两个。
VIEW_W, VIEW_H = 200, 380


def _ellipse(cx: float, cy: float, rx: float, ry: float, rot: float = 0) -> str:
    t = f' transform="rotate({rot} {cx} {cy})"' if rot else ""
    return f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}"{t}/>'


def _rounded(x: float, y: float, w: float, h: float, r: float = 6) -> str:
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}"/>'


FRONT: dict[str, list[str]] = {
    "颈": [_rounded(90, 52, 20, 14, 5)],
    "肩": [_ellipse(64, 82, 17, 14, -18), _ellipse(136, 82, 17, 14, 18)],
    "胸": [_ellipse(85, 96, 16, 15), _ellipse(115, 96, 16, 15)],
    "二头": [_ellipse(55, 118, 11, 24, -6), _ellipse(145, 118, 11, 24, 6)],
    "前臂": [_ellipse(48, 168, 9, 26, -4), _ellipse(152, 168, 9, 26, 4)],
    "腹部": [_rounded(84, 114, 32, 54, 10)],
    "腿": [_ellipse(84, 232, 20, 44), _ellipse(116, 232, 20, 44)],
    "小腿": [_ellipse(84, 310, 14, 32), _ellipse(116, 310, 14, 32)],
}

BACK: dict[str, list[str]] = {
    "颈": [_rounded(90, 52, 20, 14, 5)],
    "肩": [_ellipse(64, 82, 17, 14, -18), _ellipse(136, 82, 17, 14, 18)],
    "背": [_rounded(76, 74, 48, 62, 14)],
    "三头": [_ellipse(55, 118, 11, 24, -6), _ellipse(145, 118, 11, 24, 6)],
    "前臂": [_ellipse(48, 168, 9, 26, -4), _ellipse(152, 168, 9, 26, 4)],
    "臀": [_ellipse(87, 168, 16, 18), _ellipse(113, 168, 16, 18)],
    "腿": [_ellipse(84, 238, 20, 42), _ellipse(116, 238, 20, 42)],
    "小腿": [_ellipse(84, 310, 15, 33), _ellipse(116, 310, 15, 33)],
}

# 非肌群的填充件，给轮廓一点人形感
FILLER = [
    '<circle cx="100" cy="30" r="19"/>',            # 头
    _ellipse(45, 200, 8, 10),                        # 手
    _ellipse(155, 200, 8, 10),
    _ellipse(84, 352, 11, 12),                       # 脚
    _ellipse(116, 352, 11, 12),
]

FRONT_ONLY = {"胸", "腹部", "二头"}
BACK_ONLY = {"背", "三头", "臀"}


def _level(value: float, vmax: float, buckets: int = 5) -> int:
    if not value or vmax <= 0:
        return 0
    import math
    return min(buckets, max(1, math.ceil(value / vmax * buckets)))


def _view(shapes: dict[str, list[str]], values: dict[str, float], vmax: float,
          label: str) -> str:
    parts = [f'<g class="body-filler">{"".join(FILLER)}</g>']
    for group, figures in shapes.items():
        v = values.get(group, 0)
        lvl = _level(v, vmax)
        tip = f"{group}：{fmt_num(v)} 组" if v else f"{group}：本周未练"
        parts.append(f'<g class="muscle heat-{lvl}"><title>{esc(tip)}</title>'
                     + "".join(figures) + "</g>")
    return (f'<figure class="bodyview"><svg viewBox="0 0 {VIEW_W} {VIEW_H}" '
            f'role="img" aria-label="{esc(label)}">' + "".join(parts)
            + f'</svg><figcaption>{esc(label)}</figcaption></figure>')


def body_heatmap(group_sets: dict[str, float]) -> str:
    """按肌群有效组数着色的正面 + 背面视图。

    注意：胸/腹/二头只在正面显示，背/三头/臀只在背面显示，
    肩/腿/小腿/前臂/颈两边都显示 —— 免得用户以为某块没练。
    """
    values = {g: v for g, v in group_sets.items() if v}
    vmax = max(values.values()) if values else 0

    front_vals = {g: v for g, v in values.items() if g not in BACK_ONLY}
    back_vals = {g: v for g, v in values.items() if g not in FRONT_ONLY}

    # 注意：这里必须用 inline background，不能复用 .heat-N。
    # .heat-N 设的是 SVG 的 fill 属性，对 HTML 的 <i> 不起作用，
    # 用它色块会完全看不见。
    legend_items = "".join(
        f'<span class="lg"><i style="background:var(--heat-{i})"></i></span>'
        for i in range(6))

    untrained = [g for g in list(FRONT) + list(BACK)
                 if g not in values and g not in ("颈",)]
    untrained = sorted(set(untrained))
    note = (f'<p class="muted small">本周没练到：{"、".join(untrained)}</p>'
            if untrained else "")

    return (f'<div class="bodymap">'
            + _view(FRONT, front_vals, vmax, "正面")
            + _view(BACK, back_vals, vmax, "背面")
            + f'<div class="bodymap-legend">'
              f'<span class="muted small">未练</span>{legend_items}'
              f'<span class="muted small">练得多</span></div>'
            + f'</div>{note}')
