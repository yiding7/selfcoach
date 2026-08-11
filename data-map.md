# 数据地图与维护手册

> **这份是「要动手改文件时才翻」的参考手册** —— 每个目录装什么、jsonl 逐字段
> 长什么样、怎么手工维护、出问题查哪里。
>
> **想知道「数据从哪来、我要做什么、多久一次」，看 README 的
> [你要自己填什么](README.md#你要自己填什么)** —— 那一节够日常用了，
> 不用读这份。
>
> 最后更新：2026-08-11

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
| `data/dice.jsonl` | **食物骰子摇过什么**（是「摇过」不是「吃过」，见 §7）| `hc dice` |
| `data/profile.json` | 性别、**出生年月**、身高、目标、训练计划与周起始日 —— 引擎算心率区间要用 | `hc setup` |
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
| `profile/dish-pool.local.json` | 食物骰子的个人候选池（同名覆盖通用池）|
| `profile/family-health-context.md` | 家族史 |
| `profile/coach-journal/YYYY-MM.jsonl` | **教练工作日志** —— 线索层，**不是事实**。见 §6 |

> ⚠️ 除 `coach-journal/` 外，`profile/` 里的文件都是**事实**。
> `coach-journal/` 是助手随手记的笔记，权威性排在最底下，单独看 §6。

### `knowledge/` —— 通用知识（**进版本库**，任何人可用）

按主题分五个区，外加你自己的资料区。导航见 `knowledge/README.md`。

| 路径 | 装什么 |
|---|---|
| `knowledge/coach/persona.md` | 教练人格**核心** —— 说什么、以什么为准、什么绝对不做。不可选 |
| `knowledge/coach/personas/*.md` | **语气层** —— 怎么说。四选一，见 `data/profile.json` 的 `persona`。`hc persona` 看当前值，`hc setup` 或 `hc persona --set` 切换 |
| `knowledge/coach/safety-boundaries.md` | 医疗边界与就医升级条件 |
| `knowledge/coach/capability-matrix.md` | 不同宿主能干什么（生图、读 PDF…），报告降级时按它说明 |
| `knowledge/measurement/load-measurement.md` | **负荷计量规程** —— 绳索器械的传动比怎么测、记什么口径。同一动作换机位数字差一倍，这份解决它 |
| `knowledge/measurement/measurement-protocol.md` | 围度测量规程：量在哪、怎么读数 |
| `knowledge/movements/*.json` | 动作分类、动作模式、结构平衡规则（`hc classify` `hc next` 读）|
| `knowledge/training/training-landmarks.json` | 周容量参考区间（MEV/MAV/MRV）+ 次数区间。`hc next` 读它 |
| `knowledge/training/hr-zones.json`　`bodyweight-factors.json` | 心率区间、自重动作的体重系数 |
| `knowledge/nutrition/dish-pool.json` | 食物骰子的通用候选池（只存分档，不存热量克数）|
| `knowledge/nutrition/purine-reference.json` | 嘌呤实测值（USDA/ODS），**分档只以它为准** |
| `knowledge/nutrition/dish-composition.json`　`nutrition-reference.json` | 菜品配比表、食材营养（USDA，熟重口径）|
| `knowledge/library/` | **你自己的零散资料（不进版本库）** —— 见其 `README.md` 和 `INDEX.md` |
| `knowledge/<某某知识库>/` | **整套现成的资料库**，保持原样丢在根上（不进版本库）|

> `knowledge/` 根目录是**白名单**：只有上面五个区、`library/` 和 `README.md`
> 会进版本库，别的一律被 `.gitignore` 忽略。所以整套资料丢在根上是安全的 ——
> 放错位置的后果是「被忽略」，不是「被公开提交」。
>
> **文件格式要不要装依赖**（PDF 要 poppler，docx/xlsx 不用装）见
> `knowledge/library/README.md`。

### `reports/` —— 生成物（**不进版本库**）

| 路径 | 装什么 |
|---|---|
| `reports/<期间>.html` | 自包含 HTML 报告，断网可看 |
| `reports/<期间>.facts.json` | 引擎算出的全部事实，报告叙述的唯一数字来源 |

---

## 2. 格式规范（照着这个格式手工维护也不会出错）

> **可以直接打开抄的样例在 [`examples/`](examples/README.md)** ——
> 训练速记、体重围度、苹果健康指标、饮食各一份，`tests/test_examples.py`
> 保证它们不会烂掉。没有训记的用户从那份 README 开始最省事。
>
> ⚠️ 样例**故意不放在 `data/` 里**：`store` 按 `data/**/*.jsonl` 通配读，
> 样例搁进去会被当成真实数据算进趋势。照着格式写进真文件，别拷样例文件。

所有 `.jsonl` 都是**一行一条 JSON**，没有外层数组，没有逗号。
`id` 是去重键 —— **同 `id` 会覆盖，不会重复插入**，所以重复导入是安全的。

### 身体数据 `data/body/YYYY.jsonl`

```json
{"schema":"ha.body/1","id":"apple:weight:2026-01-08","source":"apple_health","date":"2026-01-08","type":"weight","value":75.0,"unit":"kg","label":"体重"}
```

| 字段 | 说明 |
|---|---|
| `type` | 见下表 |
| `source` | `apple_health` / `xunji` / `medical_report` / `self` 自测 / `partner` 家人代测 / `tailor` 定制店 / `manual` |
| `id` | 建议 `来源:type:日期`，例如 `manual:weist:2026-08-11` |
| `unit` | `kg` / `%` / `cm` |

**`type` 取值**

| 类别 | 取值 |
|---|---|
| 体成分 | `weight` 体重／`bodyfat` 体脂率／`lean_mass` 去脂体重／`bmi` |
| 围度（cm）| **`weist` 腰围**／`bot` 臀围／`neck` 颈围／`chest` 胸围／`hip` 胯围／`torso_narrowest` 上身最窄处／`upper_arm` 上臂围／`wrist` 手腕围／`thigh` 大腿围／`knee` 膝围／`calf` 小腿围／`head` 头围 |

> ⚠️ **腰围是 `weist`、臀围是 `bot`** —— 训记的历史拼写。不要"改正"成 `waist` /
> `hip`（`hip` 已经被胯围占用了），改了写回训记时会对不上字段。

> **`source` 是参考字段，不强制填。** 但填了的话：
> **不同 `source` 的围度读数不要放进同一条趋势线逐点比较** ——
> 换测量者相当于换了把尺，能差 1–3 cm。缺 `source` 时按「来源不明」处理，
> 同样别拿去和自测值逐点比。测量方法见 `knowledge/measurement/measurement-protocol.md`。

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

手工造这个不现实（一次训练几十个嵌套对象）—— **用 `./scripts/hc log`**，
它接受人话式的速记文本，解析后会先给你摘要确认再写入。

```bash
./scripts/hc log --syntax                              # 完整速记语法
./scripts/hc log --file examples/workout.txt --dry-run # 拿样例试一遍，不写入
```

速记样例见 [`examples/workout.txt`](examples/workout.txt)。三个最容易踩的：
**哑铃按单只手的重量写**、`~` 开头的热身组不计入有效容量、
**只有第一行 `#` 是标题行**（之后的 `#` 都只当注释）。

### 饮食 `data/meals/YYYY-MM.jsonl`

```json
{"schema":"ha.meal/1","id":"manual:2026-08-04:lunch:1","source":"manual","date":"2026-08-04",
 "meal":"lunch","name":"鸡胸肉","amount":150,"unit":"g","kcal":165,
 "protein_g":31,"fat_g":4.0,"carb_g":0,"confidence":"estimated"}
```

`meal` ∈ `breakfast` `lunch` `dinner` `snack` `other`。
`confidence` 用 `measured`（有营养成分表）或 `estimated`（目测）。

---

## 3. 各类数据的操作细节

> 「多久动一次手」在 README 的[你要自己填什么](README.md#你要自己填什么)。
> 这一节只讲那边放不下的操作细节和坑。

### ① 体重

- 落地位置：`data/body/YYYY.jsonl`，`type: "weight"`
- 核对：`./scripts/hc status` 会显示「体重数据最新到 X」
- 称重建议：**晨起空腹**。导入器对体重取当天**最早**一条，就是为了这个。
  晚上称会高 1 kg 以上，会污染均线。

> 判断永远看 7 日均线，不看单日 —— 日间波动可以轻松到 ±1.8 kg 这个量级，
> 拿单日读数做判断会得出垃圾结论。

### ② 量体（围度）—— 三条路选一条

| 方式 | 怎么做 | 到达位置 |
|---|---|---|
| **A. 训记 app**（推荐）| app 里记围度，字段 `weist` | 自动同步 → `data/body/` |
| **B. Apple Health** | 健康 → 身体测量 → 腰围 | 下次 `hc import-health` 时进来 |
| **C. 直接跟助手说** | 「今天腰围 90」 | 助手写进 `data/body/YYYY.jsonl` |

> 目前没有 `hc body add` 这样的命令，本地录入只能走上面 A/B/C。
> 需要的话可以加一个，说一声就行。

### ③ 体检报告

```bash
cp ~/Downloads/体检报告.pdf profile/medical/2026-11-15-体检.pdf
```

文件名带日期之后，跟助手说：

> 我放了一份新的体检报告在 profile/medical/，解析一下，和上一次逐项对比。

助手会做三件事：把异常项和历史对比、把围度等结构化数值写进 `data/body/`
（`source: "medical_report"`）、更新 `profile/personal-context.md` 的病史章节。

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

#### ⚠️ 绳索器械：口径不一致会造出假结论

面拉、三头下压、绳索夹胸、高位下拉这类**滑轮组**动作，配重片上的数字取决于
机器传动比。同一个动作换台机位，标 40 kg 和标 18 kg 可以是同一个力，
而脚本会老实按名字配对、算出「估算 1RM ↓ 50%」—— 数字没错，含义是假的。

**记录口径：记手上的力 = 标称重量 ÷ 传动比。** 传动比一台机器测一次就够，
最省事的办法是数行程（手移动的距离 ÷ 配重片上升的距离）。
完整方法、其他不可比的坑（史密斯杆重、单侧、辅助配重）见
**`knowledge/measurement/load-measurement.md`**。

**已经混过口径的不用回头改原始数据** —— `hc calib` 会在读取时折算，见下。

#### 负荷口径归一化 `data/load-calibration.jsonl`

`hc compare` 和 `hc doctor` 会自动扫可疑跳变，判据是**两个条件同时成立**：

| 条件 | 值 | 为什么 |
|---|---|---|
| 负荷跳变 | ×≥1.7 或 ×≤0.59 | 真的变强不会一步翻倍 |
| 次数留在同一量级 | ±40% 以内 | 重量减半+次数翻倍是有意换区间，不是换尺 |
| 两次都 ≥ 10 kg | | 小重量下换一档哑铃就是巨大比值 |
| 相隔 ≤ 35 天 | | 隔一个多月翻倍是进步 |

> 这两条降噪阈值是拿真实数据调的：不加限制时 10 条命中里有 5 条是新手期的正常进步。

**四种处置，你选一个：**

```bash
# 1 改原始记录数据（去训记改那天，然后重抓那一天）
hc sync train --date 2026-07-16 --force

# 2 只改项目内的数 —— 原始文件不动，读取时折算。旧机位若是 2:1 就填 0.5
hc calib set '面拉' --date 2026-07-16 --ratio 0.5 --note '旧机位 2:1'

# 3 忽略这次该动作的对比（组数和容量照常计入）
hc calib set '面拉' --date 2026-08-10 --ignore

# 4 确认是真实数据（留痕，不再预警）
hc calib set '面拉' --date 2026-08-10 --confirm

hc calib          # 看有哪些待处理
hc calib list     # 看现有规则
```

三件要知道的：

1. **`data/training/` 的原始记录永远不改。** 折算在 `store.load_sessions()`
   读取时做，所以 `hc rebuild` 也不会把它冲掉
2. **规则文件只追加。** 改主意就新写一条 `--supersedes <旧ID>`，
   和教练工作日志一个道理 —— 半年后要能看到「当时为什么这么折算」
3. **不要把器械写进动作名。** `面拉（龙门2:1）` 会把同一个动作拆成两条曲线，
   纵向进步就永远看不出来了。归一化才是对的做法

### ⑤ 饮酒与苹果健康导入

饮酒记录**不经过训记**，只有苹果健康有 —— 导出步骤见
[README](README.md#你要自己填什么)。命令的两种用法：

```bash
./scripts/hc import-health ~/Downloads/导出.zip --dry-run   # 先看解析结果
./scripts/hc import-health ~/Downloads/导出.zip             # 确认后写入
```

同一条 zip 会顺带把这些一起带进来：

| 指标 | 去处 |
|---|---|
| 体重 / 体脂 / 腰围 | `data/body/YYYY.jsonl` |
| 饮酒 · 步数 · 睡眠 · 静息心率 · HRV · 饮水 · 运动分钟 | `data/apple-health/metrics.jsonl` |

导出文件较大，只想要增量就加 `--since 2026-08-01`。
重复导入安全 —— 去重键是 `(date, metric)`，只会覆盖不会翻倍。

> 导出的 zip 建议也放进 `profile/`（不进版本库），保留最近一两份，旧的可以删。

---

### ⑥ 训练计划与周起始日 —— `hc setup` 里填一次

`data/profile.json` 的 `training` 块：

| 字段 | 含义 | 谁读它 |
|---|---|---|
| `days_per_week` | 一周练几次 | 建议值的参考 |
| `session_minutes` | 单次时长（不含热身/通勤） | `hc next` 拿它提示「这次会不会超」，**不替你砍组** |
| `focus` | 力量为主 / 有氧为主 / 力量有氧并重 | 建议的侧重 |
| `week_start` | **周一 或 周日** | 周报分桶、骰子的每周黄灯计数 |

两件要知道的：

1. **建议值只是默认值。** `hc setup` 会按你的目标和阶段推一份并说明理由，
   但你填什么就是什么 —— 脚本不许拿建议去否定你填的值。
2. **`week_start` 是全局唯一的「一周」口径。** 整个仓库只有
   `plan.week_start_of()` 一处做周切分，就是为了避免周报按周一切、
   骰子按周日切 —— 两个「本周」对不上，任何带「本周」的数字都不能再信。
   ⚠️ 改它**不会重写已生成的旧周报**，同一个 `reports/` 里可能并存两种口径。

### `hc setup` 的几条约定

```bash
hc setup --dry-run    # 走完全部问答但一个字都不写盘，只打印会改什么
hc setup --show       # 只列「什么数据填在哪」，不进问答
```

| 项 | 怎么填 |
|---|---|
| 固定选项（性别 / 阶段 / 语气 / 侧重 / 周起始日 / 医学禁忌）| **打编号**，不用打字。多选的编号之间要有分隔符 |
| 自由输入（忌口 / 过敏 / 口味偏好）| 空格、顿号、逗号、斜杠、反斜杠、下划线、连字符、分号、竖线**都能分隔** |
| 出生年月 | `1993-11`。只填年份也行，但年龄全年可能偏大 1 岁 |
| 身高 | **只支持 cm**。填 `1.75` 会被拒绝而不是换算 —— 猜单位是造假数据最快的路 |
| 体重 | **只支持 kg**，写进 `data/body/`（不进 profile.json）。近 7 天有数据时回车跳过即可 |
| 一周几练 | **参考值，不是考核指标**。教练按它排计划；单周没练够不会有任何提示 |

> ⚠️ `hc setup` 会真的改档案、还会往 `data/body/` 写体重。
> 想验证它「问得对不对」用 `--dry-run`，别拿真档案试 —— 2026-08-10 就这么写坏过一次。

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

## 6. 教练工作日志 `profile/coach-journal/`

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

## 7. 食物骰子 `hc dice`

解决的是「今天吃什么」这个每天要做一遍、做完还不满意的决定。

**你需要做什么：什么都不用做。** 直接跑，它按当前时间判断餐次：

```bash
./scripts/hc dice                      # 摇一次
./scripts/hc dice --scene 外卖          # 限定场景
./scripts/hc dice --again              # 不满意，重摇（旧的作废）
./scripts/hc dice --min-protein 高      # 蛋白落后 / 练完那顿
./scripts/hc dice log                  # 最近摇过什么 + 本月破戒额度
```

### 它是约束在先、随机在后

真·随机的骰子第二周就没人用了 —— 它会摇出忌口的东西、连着三天摇出面食。
所以摇之前先跑五层筛选，**顺序不能换，越靠前越硬**。
输出里那条「筛选链」就是这个过程，每层筛掉几道都写着：

| 层 | 管什么 | 硬度 | 配在哪 |
|---|---|---|---|
| 0 场景 | 这一餐、这个场合能吃到什么 | 硬 | `--slot` / `--scene` / `--cuisine` / `--effort` |
| 1 忌口 / 过敏 | 忌口按**配比占比**拦（≥10%）；**过敏零容忍** | 硬，永不放行 | `profile/health-constraints.md` 的「## 忌口」 |
| 2 医学禁忌 | 由**实际异常指标**推出的完全不能吃 | 硬，指标恢复才解除 | `data/profile.json` 的 `diet.medical_blocks` |
| 3 目标层 | 阶段决定破戒额度和灯的权重 | 软，有额度 | `data/profile.json` 的 `diet.phase` |
| 4 偏好层 | 爱吃的加权、不爱吃的降权 | 软，只调概率 | `data/profile.json` 的 `diet.likes` / `dislikes` |
| 5 加权摇 | 蛋白密度 > 灯 > 嘌呤 > 近期摇过 | 概率 | 内置权重表 |

**第 1 层和第 2 层刻意分开**：忌口是偏好，永远不变；医学禁忌是指标推出来的，
指标恢复就该解除。合并等于让一个临时状态变成永久规则。

医学禁忌存的是 **flag** 不是菜名 —— 菜品池标中性事实（这道菜「以内脏为主」），
哪些 flag 该拦是个人的事。所以池子可以分享，禁忌不外泄。

### 破戒额度跟着阶段走，不是常数

| `diet.phase` | 红灯额度 | 为什么 |
|---|---|---|
| `减脂` | 1 次/月 | 有缺口要守，一次高热量聚餐能吃掉小半周的缺口 |
| `维持` | 2 次/月 | 没有缺口压力，可持续比严格更重要 |
| `增肌` | 4 次/月 | 要吃够，这时候把黄灯压得很低反而帮倒忙 |

阶段还会改变红黄绿灯的权重（增肌期黄灯几乎不惩罚）。
没设 `diet.phase` 会按「维持」兜底，**并在输出里明说** —— 阶段错了要能一眼看出来。
想手动定死额度就填 `diet.red_quota_per_month`。

### 三条要记住的

1. **同一餐当天再跑，默认回放上次结果，不重摇。** 决策疲劳的解药是「已经定了」，
   不是「再来一次」。想换用 `--again`，旧记录原样留在日志里。
2. **`data/dice.jsonl` 记的是「摇过」，不是「吃过」。** 饮食记录在 `data/meals/`，
   两者不要互相引用。重摇作废的那次会把破戒额度退回来 —— 因为你并没有吃。
3. **忌口只有一份真相源**：`profile/health-constraints.md` 的「## 忌口」小节，
   格式是两句：`不吃：X、Y。` 和 `过敏：Z。`。
   `data/profile.json` 的 `diet.avoid` 是历史遗留，`hc setup` 会把它并进 md 并清空 ——
   复制一份到别处，迟早有一份是旧的，然后骰子就会摇出木耳。
   **解析不出来时 `hc doctor` 会红着脸报「没有生效」**，不会打绿勾糊弄过去。

### 嘌呤分档有实测依据，不是拍脑袋

`knowledge/nutrition/purine-reference.json` —— 来自 **USDA/ODS-NIH Purine Database
Release 2.0（2025）**，462 种食物的实测 mg/100 g。菜品池的 `purine` 列按它推。

建这张表是因为嘌呤**反直觉**。红黄绿灯里「油、糖、精制碳水」凭常识判断误差不大，
嘌呤不行 —— 凭印象排会同时冤枉一批食物、放过另一批：

| 常见说法 | 实测 | |
|---|---|---|
| 鸡胸是低嘌呤 | 鸡胸 110–129 vs 牛肉 114–136 | ❌ 一个量级 |
| 痛风不能吃豆制品 | 豆腐 25–29 | ❌ 豆腐是低嘌呤 |
| 海鲜都高 | 三文鱼 77–86、鳕鱼 62–73 | ❌ 比多数肉低 |
| 啤酒嘌呤极高 | 啤酒 11.8–12.4 | ❌ 它伤尿酸靠酒精抑制排泄，不是嘌呤 |

真正「极高」的只有内脏（猪肝 557）。另外**汤才是大头** —— 嘌呤溶于水，
炖煮涮烫会把肉里的嘌呤转移到汤里，所以「不喝汤」通常比「换食材」更有效。

### 池子不够用就往里加

```bash
./scripts/hc dice list --cuisine 日料      # 先看看有什么
./scripts/hc dice add --name 潮汕牛肉丸汤 --tier 绿 --purine 中 --protein 高 \
    --cuisine 中餐 --scene 店里 --slot 午 --slot 晚 --fix "汤别喝完"
```

写进 `profile/dish-pool.local.json`（不进版本库），**同名覆盖**通用池 ——
「我家楼下那家牛肉面肉给得多」这类只对你成立的判断就该放这儿。

> ⚠️ 池子里**刻意不存热量和克数**，只存三个分档（红黄绿灯 / 嘌呤 / 蛋白密度）。
> 没有联网搜索时凭印象编营养数据是这套东西最容易出的错，
> 而骰子做筛选根本用不上具体数字。要算热量那是营养成分表的事。

---

## 附：本机的实际状态与个人化约定

数据条数、覆盖率、身高等**只属于你自己**的记录，写在 `profile/data-notes.md`
（不进版本库）。这份 data-map.md 是通用手册，任何人 clone 下来都能直接用。

随时查当前状态：

```bash
./scripts/hc status      # 数据抓到哪天、缺几天、自动同步是否在跑
./scripts/hc doctor      # 环境、凭证、本地数据、个人档案全面体检
```
