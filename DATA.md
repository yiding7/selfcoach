# 数据地图与维护手册

> 这份文档回答三个问题：**数据在哪、格式长什么样、多久要动一次手。**
>
> 最后更新：2026-08-04

---

## 0. 一分钟版本

| 你的数据 | 怎么进来 | 你要做什么 | 频率 |
|---|---|---|---|
| 力量训练 | 训记 app → 后台自动同步 | **什么都不用做** | 自动，每 3 小时 |
| 有氧 | Apple Watch → 训记 → 自动同步 | **什么都不用做** | 自动，每 3 小时 |
| 体重 | Apple Health → 训记 → 自动同步 | **什么都不用做** | 自动，每 3 小时 |
| 腰围/围度 | 自己测 → 记进训记或 Apple Health | 量一次、记一次 | **每周一次** |
| 饮酒 / 步数 / 睡眠 / 静息心率 | Apple Health 导出 zip → 手动导入 | 导出 + 跑一条命令 | **每月一次** |
| 体检报告 | PDF 丢进目录 → 让助手解析 | 拷贝文件 + 说一句话 | 每次体检后 |

**只有两件事需要你定期动手：每周量围度、每月导一次苹果健康。** 其余全自动。

核对随时用：

```bash
./scripts/hc status      # 不联网，秒回：数据抓到哪天、缺几天、自动同步还活着没
```

---

## 1. 完整位置地图

### `data/` —— 机器读写的结构化数据（**不进版本库**）

| 路径 | 装什么 | 谁写的 |
|---|---|---|
| `data/training/YYYY/YYYY-MM.jsonl` | **训练记录主存储**，一行一次训练 | `hc sync` / `hc log` |
| `data/training/raw/YYYY/YYYY-MM-DD.json` | 训记接口原始响应缓存 | `hc sync` |
| `data/training/YYYY/index.json` | 每天的抓取状态，避免重复请求 | `hc sync` |
| `data/body/YYYY.jsonl` | **体重、体脂、腰围、臀围** | `hc sync body` / `hc import-health` / 助手 |
| `data/apple-health/metrics.jsonl` | 步数、睡眠、静息心率、HRV、饮酒、饮水、运动分钟 | `hc import-health` |
| `data/meals/YYYY-MM.jsonl` | 饮食记录 | `hc sync food` / 助手 |
| `data/profile.json` | 性别、出生年、身高、目标 —— 引擎算心率区间要用 | 手工编辑 |
| `data/autosync.log` | 后台同步日志 | `hc autosync` |

> `data/` 整个目录在 `.gitignore` 里。想跨设备同步，在 `data/` 里单独 `git init`
> 一个**私有**仓库。

### `profile/` —— 人读的个人档案（**不进版本库**，只有 `*.example.md` 提交）

| 路径 | 装什么 |
|---|---|
| `profile/personal-context.md` | **主档案**：病史、用药、伤病、器材、忌口、沟通偏好 |
| `profile/health-constraints.md` | 硬约束：禁忌动作、代谢限制 |
| `profile/medical/` | **体检报告原件（PDF / 图片）**，文件名带日期 |
| `profile/body-measurement/` | 围度记录、体态照片 |
| `profile/food-traffic-light.md` | 食物红绿灯清单 |
| `profile/family-health-context.md` | 家族史 |
| `profile/coach-journal/YYYY-MM.jsonl` | **教练工作日志** —— 线索层，**不是事实**。见 §7 |

> ⚠️ 除 `coach-journal/` 外，`profile/` 里的文件都是**事实**。
> `coach-journal/` 是助手随手记的笔记，权威性排在最底下，单独看 §7。

### `knowledge/` —— 通用知识（**进版本库**，任何人可用）

| 路径 | 装什么 |
|---|---|
| `knowledge/persona.md` | 教练人格，唯一真相源 |
| `knowledge/safety-boundaries.md` | 医疗边界与就医升级条件 |
| `knowledge/library/` | **你自己的专业资料库（不进版本库）** —— 见其 `README.md` |

### `reports/` —— 生成物（**不进版本库**）

| 路径 | 装什么 |
|---|---|
| `reports/<期间>.html` | 自包含 HTML 报告，断网可看 |
| `reports/<期间>.facts.json` | 引擎算出的全部事实，报告叙述的唯一数字来源 |

---

## 2. 格式规范（照着这个格式手工维护也不会出错）

所有 `.jsonl` 都是**一行一条 JSON**，没有外层数组，没有逗号。
`id` 是去重键 —— **同 `id` 会覆盖，不会重复插入**，所以重复导入是安全的。

### 身体数据 `data/body/YYYY.jsonl`

```json
{"schema":"ha.body/1","id":"apple:weight:2026-01-08","source":"apple_health","date":"2026-01-08","type":"weight","value":75.0,"unit":"kg","label":"体重"}
```

| 字段 | 说明 |
|---|---|
| `type` | `weight` 体重／`bodyfat` 体脂率／**`weist` 腰围**／`bot` 臀围／`lean_mass` 去脂体重 |
| `source` | `apple_health` / `xunji` / `medical_report` / `manual` |
| `id` | 建议 `来源:type:日期`，例如 `manual:weist:2026-08-11` |
| `unit` | `kg` / `%` / `cm` |

> ⚠️ **腰围的字段名是 `weist`**，训记的历史拼写。不要"改正"成 `waist`，
> 改了写回训记时会对不上字段。

### 苹果健康其他指标 `data/apple-health/metrics.jsonl`

```json
{"date":"2026-08-04","metric":"steps","value":12994.0}
```

`metric` 取值：`steps` `sleep_h` `resting_hr` `hrv` `alcohol`（标准杯）
`water`（ml）`exercise_min` `bmi`。去重键是 `(date, metric)`。

### 训练记录 `data/training/YYYY/YYYY-MM.jsonl`

```json
{"schema":"ha.session/1","id":"xunji:2026-01-15:1700000000000","source":"xunji",
 "date":"2026-01-15","title":"","duration_s":3728,"kcal":208.0,
 "movements":[{"name":"悍马机正手下拉","index":1,"raw_type":"背","unilateral":false,
   "sets":[{"index":1,"kind":"work","set_type":"热","reps":10.0,"weight_kg":30.0,
            "rpe":null,"done":true,"self_weight":false}]}]}
```

手工造这个不现实 —— **用 `./scripts/hc log`**，它接受人话式的文本，
解析后会先给你摘要确认再写入。

### 饮食 `data/meals/YYYY-MM.jsonl`

```json
{"schema":"ha.meal/1","id":"manual:2026-08-04:lunch:1","source":"manual","date":"2026-08-04",
 "meal":"lunch","name":"鸡胸肉","amount":150,"unit":"g","kcal":165,
 "protein_g":31,"fat_g":4.0,"carb_g":0,"confidence":"estimated"}
```

`meal` ∈ `breakfast` `lunch` `dinner` `snack` `other`。
`confidence` 用 `measured`（有营养成分表）或 `estimated`（目测）。

---

## 3. 你的五类数据：分别录到哪、多久一次

### ① 体重 —— Apple Health → 训记 → 自动同步

**你什么都不用做。** 后台自动同步已安装，每 3 小时跑一次，
体重走的是范围查询，一次请求就拿全，很快。

- 落地位置：`data/body/YYYY.jsonl`，`type: "weight"`
- 核对：`./scripts/hc status` 会显示「体重数据最新到 X」
- 称重建议：**晨起空腹**。导入器对体重取当天**最早**一条，就是为了这个。
  晚上称会高 1 kg 以上，会污染均线。

> 判断永远看 7 日均线，不看单日 —— 日间波动可以轻松到 ±1.8 kg 这个量级，
> 拿单日读数做判断会得出垃圾结论。

### ② 量体（个人测量）—— 你唯一需要每周动手的事

如果打算把腰围当作主指标（新手期同时增肌时，它比体重更能反映腹部脂肪），
这一项通常是最缺数据的。三条路，选一条固定下来就行：

| 方式 | 怎么做 | 到达位置 |
|---|---|---|
| **A. 训记 app**（推荐）| app 里记围度，字段 `weist` | 自动同步 → `data/body/` |
| **B. Apple Health** | 健康 → 身体测量 → 腰围 | 下次 `hc import-health` 时进来 |
| **C. 直接跟助手说** | 「今天腰围 90」 | 助手写进 `data/body/YYYY.jsonl` |

**测量方法固定下来才有意义**：晨起空腹、脐水平、软尺贴皮不勒紧、呼气末读数。
自己按固定方法测的一致性比体检机构高得多 —— 不同机构之间位置和松紧未必一致，
混在一起看趋势价值有限。

**频率：每周一次，固定同一天。**

> 目前没有 `hc body add` 这样的命令，本地录入只能走上面 A/B/C。
> 需要的话可以加一个，说一声就行。

### ③ 体检报告 —— PDF 丢进目录，说一句话

```bash
cp ~/Downloads/体检报告.pdf profile/medical/2026-11-15-体检.pdf
```

**文件名必须带日期**，助手靠它排序和判断哪份最新。然后说：

> 我放了一份新的体检报告在 profile/medical/，解析一下，和上一次逐项对比。

助手会做三件事：把异常项和历史对比、把围度等结构化数值写进 `data/body/`
（`source: "medical_report"`）、更新 `profile/personal-context.md` 的病史章节。

**频率：每次体检后立刻做，别攒。**

> 体检项目往往不连续（入职体检、专项体检覆盖的项目都不同）。
> 让助手对比历次报告时，它会明确指出**哪些异常项这次根本没复测**。

### ④ 训练记录 —— 力量用训记，有氧用 Apple Watch

**你什么都不用做。** 两条流都汇进训记，后台同步每 3 小时拉一次。

- 力量：训记 app 里记 → `data/training/`
- 有氧：Apple Watch 记录 → 同步到训记 → 同一条管道进来
- 落地后可直接用：`hc summary` `hc compare` `hc next <部位>` `hc cardio`

⚠️ **训练接口限频 30 秒/天**，补历史很慢（补 20 天要 10 分钟）。
所以：**不要用 `--force` 重抓**。改了解析逻辑要重算，用离线重建：

```bash
./scripts/hc rebuild      # 用本地原始缓存重算，零网络请求
```

**唯一值得补记的是强度**。没有强度信号时，配重建议只能靠次数反推，精度差不少。

训记有两个强度字段，**实际能用的是难度，不是 RPE**：

| 字段 | 粒度 | 现状 |
|---|---|---|
| `rpe` | 每组 | app 里没有入口，实测 159 天**全空** |
| `difficulty` | **每个动作一个** | 三档：`easy` / `normal` / `hard` → 简单 / 正常 / 困难 |

所以要补就补**难度**：练完在动作上点一下，一次训练多花不到半分钟。
`hc next` 会用它压过纯次数规则 —— 标「困难」的动作即使次数到了上限也不加重，
标「简单」的则不用再磨一轮。分析引擎**不把三档换算成 RPE 数值**，
那会让三个主观档位看起来比实际精确。

> ⚠️ **计时类动作（`exetype: "record"`，如平板支撑）在训记里不需要打勾**，
> `done` 恒为 `false`，时长有时只落在 `trainedSeconds` 而 `time` 是 0。
> 两个坑都踩过，现已在 `normalize.py` / `metrics.py` 处理，别再按 `done` 过滤。

### ⑤ 饮酒 —— 只能走 Apple Health，需要你每月导一次

饮酒记录**不经过训记**，只有苹果健康有。而苹果健康的数据需要**手动导出**，
这是整套流程里唯一真正需要你定期操作的环节。

**iPhone 操作**：健康 app → 右上角头像 → 最下方「导出所有健康数据」→
生成 `导出.zip` → 隔空投送到 Mac。

**然后**：

```bash
./scripts/hc import-health ~/Downloads/导出.zip --dry-run   # 先看解析结果
./scripts/hc import-health ~/Downloads/导出.zip             # 确认后写入
```

同一条 zip 会顺带把这些一起带进来：

| 指标 | 去处 |
|---|---|
| 体重 / 体脂 / 腰围 | `data/body/YYYY.jsonl` |
| 饮酒 · 步数 · 睡眠 · 静息心率 · HRV · 饮水 · 运动分钟 | `data/apple-health/metrics.jsonl` |

**频率：每月一次。** 导出文件较大，只想要增量就加 `--since 2026-08-01`。
重复导入安全 —— 去重键是 `(date, metric)`，只会覆盖不会翻倍。

> 导出的 zip 建议也放进 `profile/`（不进版本库），保留最近一两份，旧的可以删。

---

## 4. 通过助手录入（不用记格式）

直接说人话就行，助手会转成正确格式、写进正确位置。**写入前它都会先给你看摘要。**

| 你说 | 助手做什么 |
|---|---|
| 「今天腰围 90，晨起空腹量的」 | 写 `data/body/`，`type: weist`，`source: manual` |
| 「今天练了卧推 40kg 8次 8次 7次，深蹲…」 | 走 `hc log`，解析后确认再写 `data/training/` |
| 「午饭吃了鸡胸 150g 和半碗米饭」 | 写 `data/meals/` |
| 「昨晚喝了 3 杯啤酒」 | 写 `data/apple-health/metrics.jsonl`，`metric: alcohol` |
| 「我停药了，最后一针是 6 月 2 号」 | 更新 `profile/personal-context.md` |
| 「体检报告放好了，解析一下」 | 读 `profile/medical/`，更新档案 + 写 `data/body/` |

**写回训记 app 是两步的，不能跳过**：助手先发 `dry_run` 拿摘要给你看，
你确认后才带 `confirmed` 真正写入。缺少确认时服务端会直接拒绝 —— 这是设计如此。

---

## 5. 核对与排错

```bash
./scripts/hc status              # 数据新鲜度，不联网，秒回
./scripts/hc doctor              # 环境、凭证、本地数据、个人档案全面体检
./scripts/hc autosync status     # 后台同步还活着没
./scripts/hc autosync log        # 同步日志
./scripts/hc classify --unknown-only   # 有没有动作没被认出来
```

| 症状 | 多半是 | 怎么办 |
|---|---|---|
| 报告里某部位组数偏少 | 有动作没归类 | `hc classify --unknown-only` |
| 体重曲线有断点 | 那几天没称 | 正常，均线会平滑掉 |
| 训练数据缺最近几天 | 自动同步没跑 | `hc autosync status`，必要时重装 |
| 改了解析逻辑，数据没变 | 需要重算 | `hc rebuild`（**不要重新 sync**）|
| 饮酒/睡眠停在某天 | 苹果健康该导了 | `hc import-health` |

---

## 6. 三条红线

1. **不要把 `.env` 里的训记 key 打印到对话、日志或报告里。**
2. **不要把 `data/`、`profile/`、`knowledge/library/` 提交到版本库。**
   已在 `.gitignore` 里，别手动 `git add -f`。
3. **不要用 `hc sync --force` 重抓训练历史** —— 30 秒/天限频，一年要 3 小时。
   要重算用 `hc rebuild`。

---

## 7. 教练工作日志 `profile/coach-journal/`

**这一节和前面六节的性质完全不同。** 前面讲的都是事实：体重是称出来的，
吨位是算出来的。这一节讲的是助手随手记的笔记 —— 它天然不可靠，
上周的判断下周可能被推翻。

所以它有一条独立的规矩：

> **事实只以 `data/`、`knowledge/`、`profile/` 里非 `*.example.*` 的文件为准。
> `coach-journal/` 明确排除在外，它只提供线索和近期对话摘要。**

### 你需要做什么

**什么都不用做。** 不需要触发词，不需要说「记一下」，聊着聊着助手就写了。
唯一会打断你的是「确认」—— 只有这五类才会问：

目标变更 · 硬约束增减 · 长期方案变更 · 与现有档案冲突的陈述 · 医疗新既往史

拍板之后助手才会改 `profile/`，而且**旧结论降格成历史行，不删除**。

### 格式

```jsonc
// profile/coach-journal/2026-08.jsonl
{"rec":"entry","id":"20260807-01","date":"2026-08-07","kind":"判断","topic":"训练",
 "text":"前侧 DOMS 来自哈克机负荷创新高，非后链断档",
 "evidence":["hc compare --date 2026-08-05"],"supersedes":null}
```

| 字段 | 说明 |
|---|---|
| `kind` | `观察` 事实 / `判断` 助手的推断 / `待确认` 需要你拍板 |
| `topic` | 训练 / 饮食 / 体重 / 身体状态 / 目标 / 医疗 / 其他 |
| `evidence` | 数字的出处。**日志里的数字一律不许直接用，必须重跑命令** |
| `supersedes` | 推翻了哪一条。旧条目原样保留，只是被标记 |

**只追加，不修改。** 确认、否决、推翻都是追加新行；状态是读的时候回放出来的。
所以 `hc journal` 永远不写盘 —— 这也是它能放进权限白名单的原因。

### 命令

```bash
./scripts/hc journal                  # 最近 14 天 + 全部未闭合的待确认
./scripts/hc journal --grep 嗓        # 全量检索（聊到旧话题时）
./scripts/hc journal --since 30d      # 出月报时用
./scripts/hc journal --brief          # 紧凑版，给启动时注入用
```

### 两个说了算的数字

| 常数 | 值 | 为什么 |
|---|---|---|
| 默认窗口 | **14 天** | ≈ 2 个完整 PPL 轮次 ≈ 6 次训练。更早的纵向对比该由 `hc compare` 出 |
| 待确认过期 | **60 天** | 挂满 60 天仍然显示（不删），但降级到底部并标注挂了多久 |

**未闭合的待确认不受 14 天窗口限制** —— 你今天没回答的问题，
不会因为过了两周就消失。这是这套东西最值钱的部分。

---

## 附：本机的实际状态与个人化约定

数据条数、覆盖率、身高等**只属于你自己**的记录，写在 `profile/data-notes.md`
（不进版本库）。这份 DATA.md 是通用手册，任何人 clone 下来都能直接用。

随时查当前状态：

```bash
./scripts/hc status      # 数据抓到哪天、缺几天、自动同步是否在跑
./scripts/hc doctor      # 环境、凭证、本地数据、个人档案全面体检
```
