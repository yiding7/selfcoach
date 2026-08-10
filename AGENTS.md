# selfcoach

个人健身教练 / 营养师 / 健康顾问工具。**模型无关**：所有数据和分析由确定性
Python 脚本产出，模型只负责措辞。

## 开始之前必读

- `knowledge/coach/persona.md` —— 教练人格（唯一真相源）
- `knowledge/coach/safety-boundaries.md` —— 医疗边界与就医升级条件
- `data-map.md` —— 数据地图：什么数据在哪、格式、怎么录入、多久导一次
- `knowledge/README.md` —— knowledge/ 分五个区，哪个区放什么

## 用户的私有知识库

`knowledge/library/` 是使用者自己的专业资料（教材、课程、笔记）。不进版本库。

聊到营养、训练方法、有氧、恢复这类话题时，**先看 `knowledge/library/INDEX.md`**
（存在的话），判断有没有相关资料，有就打开对应那份再回答。
不要把整个目录读一遍 —— 按 INDEX 定位。

整套现成的资料库放在 `knowledge/` 根上（根目录是 gitignore 白名单，不会误提交），
零散单份才进 `library/`。当前有一套「真理之弓的知识库」（186 份），
索引在 `knowledge/真理之弓的知识库/知识库索引.md`。
要证据等级去 `优质文献/`，别把科普短文说成文献结论。

**读文件要不要装依赖**：除 PDF 外常见格式都不用装 —— `.docx` `.xlsx` `.pptx`
`.epub` 是 zip+XML，标准库就能解正文（**图片里的文字读不到**）；
macOS 上 `textutil -convert txt -stdout` 更省事。
**PDF 要 `brew install poppler`，不装完全读不了**；音频不解析。
只在「确实需要这份 + 当场读不了 + 没有替代来源」三条同时成立时才提醒用户装，
**不自己装**，用户不装也要能继续并说明结论少了哪块证据。

优先级：`safety-boundaries.md` > `profile/health-constraints.md` >
`knowledge/library/` > 模型自己的通用知识。
资料和前两者冲突时，明确指出冲突，不要默默选一边。

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
- 不要把 `data/`、`profile/`、`knowledge/library/` 提交到版本库（已 gitignore）
- 不要在没有共同动作时比较两次训练的峰值负荷（器械和自由重量不可比）
- 不要在 RPE 覆盖率不足时输出强度评价
- 改了解析逻辑用 `hc rebuild` 离线重算，不要重新同步（一年要 3 小时）
