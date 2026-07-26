# Health Assistant · 健身教练 / 营养师 / 健康顾问 一体化 AI 工具

> 一套**模型无关、即启即用**的个人健康基建。
> 训练数据自动同步 → 确定性引擎算出同部位对比与下次训练处方 → 生成图文并茂的周/月/年报 →
> 由你接入的任意模型套上教练人格来讲解。

**English**: [README.en.md](README.en.md)

---

## 它是什么

不是一个 App，也不是一个绑定某家模型的服务。它是一个 **Skill 仓库 + 一套纯标准库脚本**：

- **换模型不换结果** —— 所有分析、指标、图表都由确定性 Python 脚本产出。模型只负责把结论讲得好听，不负责算数。换 Claude、GPT、DeepSeek、Kimi、本地 Ollama，报表数字完全一致。
- **零依赖** —— 只用 Python 标准库。不需要 `pip install`，不需要 matplotlib，clone 下来就能跑。
- **零外链** —— 生成的 HTML 报表把图表以内联 SVG 写死在文件里。断网能看，存档十年后还能看，Cmd+P 直接存 PDF。
- **有没有训记都能用** —— 有训记 API key 就自动同步；没有就用自然语言手记，功能一样完整。

## 5 分钟上手

```bash
git clone <your-fork-url> health-assistant
cd health-assistant
./install.sh              # 建目录、把 skills 链到你的 agent 宿主
cp .env.example .env      # 有训记就填 key；没有就跳过这步
hc doctor                 # 体检：环境、连通性、key、本地数据
hc sync                   # 拉训练/体重/饮食数据（首次会说明预计耗时）
hc report weekly          # 出周报 → reports/2026-W30.html
```

然后在 Claude Code / Codex / Cursor 里直接说「帮我看看这周练得怎么样」即可。

## 没有训记怎么办

完全没问题，这是**一等公民路径**，不是降级方案：

```
你：今天练胸。卧推 60kg 做了 10/10/8，上斜哑铃 22.5kg 三组 12 个，RPE 8
助手：（解析 → 展示摘要 → 你确认 → 落库）
```

之后的同部位对比、下次处方、周月年报，跟训记同步来的数据享受完全相同的分析。

## 文档

| 文档 | 内容 |
|---|---|
| `knowledge/persona.md` | 教练人格的唯一真相源 |
| `knowledge/safety-boundaries.md` | 医疗安全边界与就医升级触发条件 |
| `knowledge/capability-matrix.md` | 可选增强能力（生图等）及安装建议 |
| `vendor-docs/` | 训记官方 Open API 文档（已脱敏） |
| `skills/` | 七个 SKILL.md，交付主体 |

## 隐私

- 四个训记 API key 只存在于 `.env`，已被 `.gitignore` 忽略，任何脚本和日志都不会打印。
- `data/` 目录（训练记录、体重、饮食、个人健康档案）默认**不进版本库**。
- 想跨设备同步私人数据：`hc export` 打包带走，或在 `data/` 里单独 init 一个私有仓库。

## 免责声明

本工具提供一般性健身与营养信息，**不构成医疗建议**，不诊断或治疗任何疾病，
不能替代医生。涉及用药、既往病史、异常症状时，请咨询专业医疗人员。

## License

MIT
