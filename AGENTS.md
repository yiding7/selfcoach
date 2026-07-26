# 健康助手

个人健身教练 / 营养师 / 健康顾问工具。**模型无关**：所有数据和分析由确定性
Python 脚本产出，模型只负责措辞。

## 开始之前必读

- `knowledge/persona.md` —— 教练人格（唯一真相源）
- `knowledge/safety-boundaries.md` —— 医疗边界与就医升级条件

## 最重要的一条

**数字归脚本，措辞归你。**

吨位、组数、估算 1RM、对比结论、训练处方，全部由 `hc` 命令算出。
不要心算，不要估算，不要引入 `facts.json` 里没有的数字。

用户可能今天用 Claude、明天用 GPT。数字由脚本产出，这份健康记录才有纵向价值。

## 命令

```bash
./scripts/hc doctor                    # 环境体检
./scripts/hc sync --since 30d          # 从训记同步（训练接口 30s/天限频，会预告耗时）
./scripts/hc log                       # 手记训练（没有训记也能用）
./scripts/hc summary --date 2026-07-12 # 单次训练指标
./scripts/hc compare                   # 本次 vs 上次同部位
./scripts/hc next 胸                    # 下次训练建议
./scripts/hc report weekly             # 自包含 HTML 报告
./scripts/hc classify --unknown-only   # 看看有哪些动作没认出来
```

## Skills

`skills/` 下有七个，入口是 `health-coach`。任何健康健身话题都从它开始。

## 千万别做

- 不要把 `.env` 里的训记 key 打印到对话、日志或报告里
- 不要把 `data/` 提交到版本库（已 gitignore）
- 不要在没有共同动作时比较两次训练的峰值负荷（器械和自由重量不可比）
- 不要在 RPE 覆盖率不足时输出强度评价
- 改了解析逻辑用 `hc rebuild` 离线重算，不要重新同步（一年要 3 小时）
