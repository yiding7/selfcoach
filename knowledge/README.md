# knowledge/ —— 通用知识

这里放**任何人都能用**的东西：教练怎么说话、动作怎么分类、嘌呤谁高谁低。
不放任何属于某一个人的东西 —— 那些在 `profile/` 和 `data/`。

这条分界线决定了这个仓库能不能安全地公开分享，所以它是硬的：
**`knowledge/` 里出现名字、病史、体检数值，就是放错地方了**
（`tests/test_persona.py` 会检查这一点）。

## 五个区

| 目录 | 谁读 | 放什么 |
|---|---|---|
| `coach/` | **模型读** | 人格核心、四种语气、医疗安全边界、能力矩阵 |
| `measurement/` | **模型读** | 怎么量：围度测量规程、负荷计量口径（绳索传动比）|
| `movements/` | 脚本读 | 动作分类、动作模式、结构平衡规则 |
| `training/` | 脚本读 | 周容量参考区间、心率区间、自重系数 |
| `nutrition/` | 脚本读 | 菜品候选池、配比表、食材营养、嘌呤实测值 |

分区的依据就是这个项目最核心的那条规矩 —— **数字归脚本，措辞归你**。
`coach/` 和 `measurement/` 是散文，模型直接读；后三个是确定性数据表，
只有 `hc` 命令读，模型不要凭印象改里面的数。

改数据表之前先看一眼 `tests/`：`purine-reference.json`、`dish-composition.json`
这些都有内容契约测试（配比要加满 100、食材名要在参考表里存在）。

## 你自己的资料放哪

**两个位置，按「这批资料是不是一个整体」分：**

- **一整套现成的知识库**（买来的课程包、别人整理好的资料库）→
  直接放 `knowledge/` 根，**保持原样不用拆**
- **零散的单份资料**（一篇论文、一份讲义、自己的笔记）→
  `knowledge/library/` 下按主题挑一个目录；不确定就丢 `library/notes/`

```
knowledge/library/
  INDEX.md      ← 你维护的总目录，助手每次先读它
  nutrition/ training/ cardio/ recovery/ reference/ notes/
```

`library/` 整个在 `.gitignore` 里，只有目录骨架和 README 会提交。
详细约定、**以及各种文件格式要不要装依赖**，见
[`library/README.md`](library/README.md)。

> **`knowledge/` 根目录是白名单。** `.gitignore` 只放行上面五个区、`library/`
> 和本文件，其余一律忽略。所以整套资料丢在根上**不会被误提交** ——
> 这正是白名单的用处：放错位置的后果是「被忽略」，而不是「被公开」。

### 能读什么、要装什么（详见 [`library/README.md`](library/README.md)）

| | 格式 | 依赖 |
|---|---|---|
| ✅ | `.md` `.txt` `.csv` `.json` `.docx` `.xlsx` `.pptx` `.epub` 图片 | **不用装** —— docx/xlsx 是 zip+XML，标准库就能解 |
| ⚙️ | `.pdf` | `brew install poppler`，**不装完全读不了** |
| — | 音频 | 不解析，当备份留着 |
