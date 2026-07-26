"""报告样式。单文件内联，零外链。

深浅色靠 CSS 变量 + prefers-color-scheme。因为 SVG 是内联的，
图表会继承这些变量，所以同一份 SVG 在两种模式下自动变色 —— 不需要生成两份。

打印样式里 print-color-adjust: exact 是必须的：没有它，Chrome 会把图表填色
全部去掉，打出来是一堆空白方块。
"""

CSS = """
:root {
  --bg: #fbfbfa;  --surface: #ffffff;  --fg: #1c1b19;  --muted: #6b6a67;
  --line: #e4e2dd;  --line-soft: #efedE8;
  --accent: #b8593f;
  --good: #3f7a52;  --warn: #b8863f;  --bad: #a8443a;
  --s1: #b8593f;  --s2: #3f6d7a;  --s3: #7a6a3f;  --s4: #5f5f7a;
  --s5: #3f7a52;  --s6: #7a3f5f;  --s7: #6b6a67;
  --heat-0: #eceae5;  --heat-1: #dbe6dd;  --heat-2: #b3cebc;
  --heat-3: #82b195;  --heat-4: #4f8f6d;  --heat-5: #2c6a4a;
  --band-low: rgba(184,134,63,.10);
  --band-ok: rgba(63,122,82,.12);
  --band-high: rgba(168,68,58,.10);
  --radius: 10px;
  --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace;
  --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
          "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #16171a;  --surface: #1e2024;  --fg: #e8e6e1;  --muted: #9a9892;
    --line: #2e3238;  --line-soft: #262a2f;
    --accent: #e08163;
    --good: #6bbd8a;  --warn: #d9a95e;  --bad: #e0796c;
    --s1: #e08163;  --s2: #6ba8bd;  --s3: #c4ab6b;  --s4: #9a9ac4;
    --s5: #6bbd8a;  --s6: #c47f9f;  --s7: #9a9892;
    --heat-0: #26292e;  --heat-1: #2c3b33;  --heat-2: #34543f;
    --heat-3: #3f724f;  --heat-4: #4f9265;  --heat-5: #63b17e;
    --band-low: rgba(217,169,94,.12);
    --band-ok: rgba(107,189,138,.13);
    --band-high: rgba(224,121,108,.12);
  }
}
/* 用户手动切换时要能压过系统偏好 */
:root[data-theme="light"] {
  --bg: #fbfbfa; --surface: #fff; --fg: #1c1b19; --muted: #6b6a67;
  --line: #e4e2dd; --line-soft: #efede8;
  --heat-0: #eceae5; --heat-1: #dbe6dd; --heat-2: #b3cebc;
  --heat-3: #82b195; --heat-4: #4f8f6d; --heat-5: #2c6a4a;
}
:root[data-theme="dark"] {
  --bg: #16171a; --surface: #1e2024; --fg: #e8e6e1; --muted: #9a9892;
  --line: #2e3238; --line-soft: #262a2f;
  --heat-0: #26292e; --heat-1: #2c3b33; --heat-2: #34543f;
  --heat-3: #3f724f; --heat-4: #4f9265; --heat-5: #63b17e;
}

* { box-sizing: border-box; }
body {
  margin: 0; padding: 32px 20px 64px; background: var(--bg); color: var(--fg);
  font-family: var(--sans); line-height: 1.65;
  -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 860px; margin: 0 auto; }

header.report-head { margin-bottom: 28px; }
.eyebrow { color: var(--muted); font-size: 13px; letter-spacing: .08em;
           text-transform: uppercase; margin: 0 0 4px; }
h1 { font-size: 30px; line-height: 1.25; margin: 0 0 6px; font-weight: 650;
     letter-spacing: -.01em; }
h2 { font-size: 19px; margin: 0 0 14px; font-weight: 620; letter-spacing: -.005em; }
h3 { font-size: 15px; margin: 18px 0 8px; font-weight: 620; }
p { margin: 0 0 10px; }
.muted { color: var(--muted); }
.small { font-size: 13px; }

.card {
  background: var(--surface); border: 1px solid var(--line);
  border-radius: var(--radius); padding: 20px 22px; margin: 0 0 18px;
}
.card > :last-child { margin-bottom: 0; }

.kpis { display: grid; gap: 10px; margin-bottom: 18px;
        grid-template-columns: repeat(auto-fit, minmax(132px, 1fr)); }
.kpi { background: var(--surface); border: 1px solid var(--line);
       border-radius: var(--radius); padding: 13px 15px; }
.kpi .k { color: var(--muted); font-size: 12px; margin-bottom: 3px; }
.kpi .v { font-size: 23px; font-weight: 620; font-variant-numeric: tabular-nums;
          letter-spacing: -.02em; line-height: 1.2; }
.kpi .s { font-size: 12px; color: var(--muted); margin-top: 2px; }
.kpi .v.up { color: var(--good); } .kpi .v.down { color: var(--bad); }

table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { text-align: left; padding: 7px 9px; border-bottom: 1px solid var(--line-soft); }
th { color: var(--muted); font-weight: 560; font-size: 12.5px; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums;
                 font-family: var(--mono); font-size: 13px; }
tbody tr:last-child td { border-bottom: none; }
.scroll-x { overflow-x: auto; -webkit-overflow-scrolling: touch; }

.chart { width: 100%; height: auto; display: block; margin: 6px 0 4px; }
.grid { stroke: var(--line-soft); stroke-width: 1; }
.tick { fill: var(--muted); font-size: 11px; font-family: var(--sans); }
.barval { fill: var(--muted); font-size: 10.5px; font-family: var(--mono); }
.line { fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
.dot { stroke: var(--surface); stroke-width: 1.2; }
.dot.raw { fill: var(--muted); opacity: .38; stroke: none; }
.band.low { fill: var(--band-low); } .band.ok { fill: var(--band-ok); }
.band.high { fill: var(--band-high); }
.s1 { stroke: var(--s1); fill: var(--s1); } .s2 { stroke: var(--s2); fill: var(--s2); }
.s3 { stroke: var(--s3); fill: var(--s3); } .s4 { stroke: var(--s4); fill: var(--s4); }
.s5 { stroke: var(--s5); fill: var(--s5); } .s6 { stroke: var(--s6); fill: var(--s6); }
.s7 { stroke: var(--s7); fill: var(--s7); }
rect.bar { stroke: none; }
.arc.s1, .arc.s2, .arc.s3, .arc.s4, .arc.s5, .arc.s6, .arc.s7 { fill: none; }
.donut-main { fill: var(--fg); font-size: 19px; font-weight: 620;
              font-family: var(--sans); }
.donut-sub { fill: var(--muted); font-size: 11px; font-family: var(--sans); }
.spark { vertical-align: middle; }
.spark .line { stroke-width: 1.6; fill: none; }
.spark .line.up { stroke: var(--good); } .spark .line.down { stroke: var(--bad); }

.cell { stroke: none; }
.heat-0 { fill: var(--heat-0); } .heat-1 { fill: var(--heat-1); }
.heat-2 { fill: var(--heat-2); } .heat-3 { fill: var(--heat-3); }
.heat-4 { fill: var(--heat-4); } .heat-5 { fill: var(--heat-5); }

.bodymap { display: flex; gap: 8px; align-items: flex-end; flex-wrap: wrap;
           justify-content: center; }
.bodyview { margin: 0; flex: 1 1 150px; max-width: 210px; text-align: center; }
.bodyview svg { width: 100%; height: auto; }
.body-filler { fill: var(--heat-0); }
.muscle { stroke: var(--surface); stroke-width: 1.5; }
.bodymap-legend { display: flex; align-items: center; gap: 4px; width: 100%;
                  justify-content: center; margin-top: 6px; }
.bodymap-legend i { display: inline-block; width: 17px; height: 11px;
                    border-radius: 2px; }
.bodymap-legend .lg { display: inline-flex; align-items: center; gap: 3px;
                      font-size: 11px; color: var(--muted); }
.heat-0i { background: var(--heat-0); }

.findings { list-style: none; padding: 0; margin: 0; }
.findings li { padding: 9px 0 9px 26px; position: relative;
               border-bottom: 1px solid var(--line-soft); }
.findings li:last-child { border-bottom: none; }
.findings li::before { position: absolute; left: 2px; top: 9px;
                       font-family: var(--mono); font-size: 13px; }
.f-good::before { content: "✓"; color: var(--good); }
.f-bad::before  { content: "△"; color: var(--warn); }
.f-act::before  { content: "→"; color: var(--accent); }
.f-info::before { content: "·"; color: var(--muted); }

.pill { display: inline-block; padding: 1px 8px; border-radius: 999px;
        font-size: 11.5px; border: 1px solid var(--line); color: var(--muted);
        vertical-align: middle; }
.pill.good { color: var(--good); border-color: var(--good); }
.pill.bad { color: var(--bad); border-color: var(--bad); }

.up { color: var(--good); } .down { color: var(--bad); } .flat { color: var(--muted); }

.narrative { border-left: 3px solid var(--accent); padding: 2px 0 2px 15px;
             margin: 0 0 16px; }
.narrative p:last-child { margin-bottom: 0; }
.data-only { font-size: 12.5px; color: var(--muted); border: 1px dashed var(--line);
             border-radius: var(--radius); padding: 10px 13px; margin-bottom: 18px; }

.legend { display: flex; gap: 12px; flex-wrap: wrap; font-size: 12px;
          color: var(--muted); margin-top: 4px; }
.legend span { display: inline-flex; align-items: center; gap: 4px; }
.legend i { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }

footer.disclaimer { margin-top: 32px; padding-top: 16px;
                    border-top: 1px solid var(--line); color: var(--muted);
                    font-size: 12.5px; }

@media (max-width: 560px) {
  body { padding: 20px 13px 48px; }
  h1 { font-size: 24px; }
  .card { padding: 16px 14px; }
}

@media print {
  :root {
    --bg: #fff; --surface: #fff; --fg: #111; --muted: #555;
    --line: #ddd; --line-soft: #eee;
    --heat-0: #eee; --heat-1: #dbe6dd; --heat-2: #b3cebc;
    --heat-3: #82b195; --heat-4: #4f8f6d; --heat-5: #2c6a4a;
  }
  /* 没有这行，Chrome 会把图表填色全部去掉，打出来是空白方块 */
  * { -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important; }
  @page { size: A4; margin: 14mm; }
  body { padding: 0; font-size: 12px; }
  .card, .kpi, figure, table, .bodymap { break-inside: avoid; }
  h1, h2, h3 { break-after: avoid; }
  .no-print { display: none !important; }
  a::after { content: ""; }
}
"""
