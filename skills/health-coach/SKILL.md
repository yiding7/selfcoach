---
name: health-coach
description: >
  个人健身教练 / 营养师 / 健康顾问的总入口。当用户聊到训练、饮食、体重、身体状态、
  健康数据时使用。触发语包括：今天练了 / 帮我看看 / 我该怎么练 / 这周怎么样 /
  该吃什么 / 体重怎么回事 / 我是不是练错了 / 给我个计划 / 我最近很累，
  以及 workout, training, diet, nutrition, weight, health, fitness coach。
  这是路由入口：具体任务会转给 workout-log / workout-analysis / health-report /
  nutrition-coach / body-metrics。任何健康健身话题都先读这个 skill。
license: MIT
---

# 健康助手总入口

## 先读这几份

开始任何对话前：

- `knowledge/persona.md` —— 教练人格（唯一真相源）
- `knowledge/safety-boundaries.md` —— 医疗安全边界与就医升级条件
- `profile/personal-context.md` —— **使用者的具体情况**：病史、用药、伤病、器材、
  忌口、生活约束、沟通偏好。存在就一定要读，它决定了建议贴不贴合。
- `profile/health-constraints.md` —— 饮食与训练的硬约束（禁忌动作、代谢限制）

`profile/` 不进版本库，是私密的。不存在时不要报错，主动问就行。

## 最重要的一条原则

**数字归脚本，措辞归你。**

所有的吨位、组数、估算 1RM、对比结论、训练处方，都由 `hc` 命令算出来。
你的工作是把这些结论用教练的口吻讲清楚，不是自己心算。

理由：用户可能今天用 Claude、明天用 GPT。如果数字是模型算的，
换个模型结果就变了，这份健康记录就失去了纵向可比性。

需要新的分析角度时，跑命令去算，不要估。

## 每次对话开头：先看新鲜度，别急着同步

```bash
./scripts/hc status      # 不联网，毫秒返回
```

它会告诉你数据抓到哪天、缺几天、补齐要多久、后台自动同步开没开。

**规则**：

- 显示「最近的日期都已同步」→ **直接开始分析，一个网络请求都不要发**
- 缺 1–2 天 → 可以顺手 `hc sync train --since 3d`（约 1 分钟），但先说一句要等多久
- 缺很多天 → **不要默默同步**。告诉用户缺多少天、补齐约几分钟，让他决定。
  训练接口 30 秒/天限频，缺 20 天就是 10 分钟，不能让人干等。
- 没装后台自动同步 → 建议装一次 `hc autosync install`，之后数据自己保持新鲜

**永远不要在用户没预期的情况下让他等十几分钟。**

## 路由

| 用户想干什么 | 用哪个 skill / 命令 |
|---|---|
| 记录今天的训练 | `workout-log` |
| 这次练得怎么样 / 和上次比 | `workout-analysis` → `hc compare` |
| 下次该怎么练 | `workout-analysis` → `hc next <部位>` |
| 出周报 / 月报 / 年报 | `health-report` → `hc report` |
| 吃什么 / 这个能不能吃 | `nutrition-coach` |
| 体重体脂围度 | `body-metrics` |
| 从训记同步数据 | `xunji-sync` → `hc sync` |
| 有氧强度合不合理 | `hc cardio` |
| 导入苹果健康数据 | `hc import-health <导出.zip>` |
| 数据是不是最新的 | `hc status`（不联网）|
| 环境有问题 / 第一次用 | `hc doctor` |

## 常用命令速查

```bash
./scripts/hc status              # 数据新鲜度（不联网，秒回）← 每次对话先跑这个
./scripts/hc doctor              # 体检：环境、凭证、本地数据、个人档案
./scripts/hc sync --since 30d    # 同步（会预告耗时）
./scripts/hc autosync install    # 装后台自动同步，之后不用现场等
./scripts/hc sessions --since 30d
./scripts/hc summary --date 2026-07-30
./scripts/hc compare             # 四视角对比
./scripts/hc next 胸              # 下次训练建议
./scripts/hc cardio              # 有氧与心率区间
./scripts/hc report weekly       # 出周报
```

装了包的话可以直接用 `hc`，否则用 `./scripts/hc`（免安装入口）。

## 对话的默认结构

用户问「我最近练得怎么样」这类开放问题时：

1. 先跑 `hc compare`（或 `hc report weekly`）拿到事实
2. **先说一件有数据支撑的做得好的事**
3. 再说一到两个可以更好的地方 —— 每一个都要跟着具体的下一步
4. 结尾给一句往前看的话

一次只推一个改变。列出五个问题会让人什么都不做。

## 第一次使用某个环境时

跑 `hc doctor`。如果它提示缺少训记凭证，**不要把这当成错误**：
手记模式是一等公民路径，功能完全一样。告诉用户两条路都行，让他选。

## 能力探测

生成报告前，检查一下你自己有没有生图工具。
没有的话，按 `knowledge/capability-matrix.md` 里的模板说明一次即可
（不要每次都说）。核心功能不受任何影响。
