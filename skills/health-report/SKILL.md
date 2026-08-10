---
name: health-report
description: >
  生成周报 / 月报 / 年报，图文并茂的自包含 HTML，并为报告写教练叙述。
  触发语：出个周报 / 这周总结 / 这个月怎么样 / 年度总结 / 给我个报告 /
  帮我看看这段时间，以及 weekly report, monthly summary, annual review,
  generate report, progress report。
license: MIT
---

# 生成报告

## 两步

```bash
./scripts/hc report weekly                    # 出报告 + facts.json
./scripts/hc report monthly --date 2026-07
./scripts/hc report yearly --date 2026-01-01
```

生成两个文件：
- `reports/2026-W30.html` —— 完整可读的报告（**没有你也是完整的**）
- `reports/2026-W30.facts.json` —— 算好的事实，给你写叙述用

然后读 facts.json，写叙述，注入：

```bash
./scripts/hc inject 2026-W30 --slot opening --file - <<'TXT'
（你写的开场）
TXT
```

槽位：`opening` `training` `body` `nutrition` `closing`，都可选，写哪个填哪个。

## 写叙述的铁律

**只能用 facts.json 里出现过的数字。**

不要心算，不要估算，不要引入任何新数字。需要新角度就跑命令去算。

理由：用户可能今天用 Claude、明天用 GPT。数字由脚本产出才能保证纵向可比。
你负责的是措辞，不是运算。

同样地：不要发明新结论。`findings` 里已经有优点/缺点/改进点，
你的工作是把它们讲得好懂、有温度，不是加几条自己的判断。

## 叙述的结构

- **opening**：两三句。先说一件有数据支撑的做得好的事，再点出本期主线。
- **training**：把 findings 串成一段话，而不是复述表格。表格已经在报告里了。
- **body**：体重趋势要用 7 日均线说，不要说单日读数。
- **nutrition**：没有饮食数据时就说没有，别硬凑。
- **closing**：一句往前看的话，加**一个**具体的下周重点。一个就够。

## 必须诚实的地方

报告里的 `data_quality` 段落会说明本期的盲区（同步覆盖率、RPE 覆盖率、
未分类动作、缺体重导致的容量不完整）。**不要在叙述里绕过这些。**
把盲区说清楚，上面的结论才敢用。

## 配图

报告里的图表全部是内联 SVG，代码渲染，深浅色自适应，可打印成 PDF。
生成前检查一下你有没有生图工具：没有就按 `knowledge/coach/capability-matrix.md`
的模板提一句（一次就够），核心内容不受影响。

⚠️ 即使有生图能力，**动作要领示意图也建议继续用手绘 SVG**。
扩散模型画的健身动作经常关节角度是错的，错误的动作示范图在教练报告里是有害的。
