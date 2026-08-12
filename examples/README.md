# 没有训记，怎么把数据记进来

**手记不是降级方案。** 记进来的数据和训记同步来的走完全相同的分析流程 ——
`hc compare`、`hc next`、`hc report` 一个都不少。

> ⚠️ **这个目录里的文件是样例，不是数据。**
> 它们**故意**放在 `examples/` 而不是 `data/` —— `data/` 下的 `*.jsonl` 会被
> `store` 通配读取，样例放进去会被当成你的真实体重、真实训练算进趋势里。
> 要用就**照着格式往 `data/` 里的真文件写**，不要把这些文件拷过去。
>
> 反过来也一样：`examples/` **进版本库**，所以这里的数字全是编的 ——
> 重量取整五整十、围度是整数、日期一律 `2026-01-01`，一眼就看得出不是谁的记录。
> 改样例时别顺手贴一段自己的真实记录进来，一旦推上去就是公开的健康数据。

四类数据，路子不一样：

| 数据 | 怎么记 | 落到哪 |
|---|---|---|
| **训练** | `hc log`（有解析器，推荐）| `data/training/YYYY/YYYY-MM.jsonl` |
| **体重 / 围度 / 体脂** | 跟助手说一句，或手写 jsonl | `data/body/YYYY.jsonl` |
| **步数 / 睡眠 / 饮酒 / 饮水** | `hc import-health <导出.zip>`，或手写 | `data/apple-health/metrics.jsonl` |
| **饮食** | 跟助手说一句，或手写 jsonl | `data/meals/YYYY-MM.jsonl` |

**最省事的路子始终是跟助手说人话**：「今天腰围 80」「昨晚喝了 2 杯啤酒」。
它会转成下面这些格式、写进正确位置，**写之前会先给你看摘要**。
下面这些是给你想自己动手时用的。

---

## 1. 训练 —— 用 `hc log`

手工造训练 jsonl 不现实（一次训练几十个嵌套对象），所以有速记语法：

```bash
./scripts/hc log --syntax                              # 完整语法
./scripts/hc log --file examples/workout.txt --dry-run # 拿样例试一遍，不写入
./scripts/hc log                                       # 直接敲，Ctrl-D 结束
```

样例见 [`workout.txt`](workout.txt)。一分钟版本：

```
# 2026-01-01 胸+三头 19:00-20:00

杠铃卧推 ~40x10 60x10 60x10 60x10
上斜哑铃卧推 15x10x3
双杠臂屈伸 BWx12 BW+10x8
平板支撑 T:60s x3
保加利亚分腿蹲 L:20x10 R:25x10

> 状态一般，最后一个动作减了一组
```

几条容易踩的：

- **哑铃按单只手的重量写**，解析器知道要乘二。写成合计会让所有纵向对比失真
- `~` 开头是热身组，**不计入有效容量**
- **标题行是「动作之前」那条带日期或起止时间的 `#`。** 只写名字的标题
  （`# 推日`）仅限文件第一行；其余 `#` 无论整行还是行尾都只当注释。
  所以「第一行是句注释、第二行才是真标题」也能认对
- 一条带日期的 `#` 没被采用时会给一条 **warning**，不会静默 ——
  日期悄悄变成今天，训练会落进错误那一天、进错误那一周的报告
- 日期、名字、时间三样都能省。整行 `#` 都省掉就按今天算
- 中途降重量就照实写（`25x10 25x10 20x12`），别把三组抹成同一个数 ——
  抹平会污染估算 1RM
- `--dry-run` 先看解析结果再落库。解析错了你一眼能看见，这是这套设计的重点

---

## 2. 体重、围度、体脂 → `data/body/YYYY.jsonl`

一行一条 JSON，**没有外层数组，没有逗号**。样例见 [`body.jsonl`](body.jsonl)：

```json
{"schema":"ha.body/1","id":"self:weight:2026-01-01","source":"self","date":"2026-01-01","type":"weight","value":70.0,"unit":"kg","label":"体重"}
{"schema":"ha.body/1","id":"self:weist:2026-01-01","source":"self","date":"2026-01-01","type":"weist","value":80.0,"unit":"cm","label":"腰围"}
```

- `id` 是去重键，**同 id 会覆盖不会重复插入**，所以重复导入是安全的。
  建议写成 `来源:type:日期`
- **腰围是 `weist`、臀围是 `bot`** —— 训记的历史拼写。别"改正"成 `waist`/`hip`
  （`hip` 已经被胯围占用了），改了写回训记会对不上字段
- `source` 不强制填，但**不同 source 的围度不要放进同一条趋势线逐点比** ——
  换测量者相当于换了把尺，能差 1–3 cm。可选值：
  `self` 自测 / `partner` 家人代测 / `tailor` 定制店 / `apple_health` / `xunji` /
  `medical_report` / `manual`
- 称重**晨起空腹**。导入器对体重取当天最早一条，就是为了这个

完整的 `type` 取值表在 [`../data-map.md`](../data-map.md#身体数据-databodyyyyyjsonl)。

## 3. 步数、睡眠、饮酒等 → `data/apple-health/metrics.jsonl`

有苹果健康就别手写，`hc import-health <导出.zip>` 一次导完。
手写的格式见 [`apple-health.jsonl`](apple-health.jsonl)，最简单的一种：

```json
{"date":"2026-01-01","metric":"steps","value":12000.0}
```

`metric` ∈ `steps` `sleep_h` `resting_hr` `hrv` `alcohol`（标准杯）`water`（ml）
`exercise_min` `bmi`。去重键是 `(date, metric)`。

## 4. 饮食 → `data/meals/YYYY-MM.jsonl`

样例见 [`meals.jsonl`](meals.jsonl)：

```json
{"schema":"ha.meal/1","id":"manual:2026-01-01:lunch:1","source":"manual","date":"2026-01-01","meal":"lunch","name":"鸡胸肉","amount":100,"unit":"g","kcal":165,"protein_g":31.0,"fat_g":4.0,"carb_g":0,"confidence":"measured"}
```

- `meal` ∈ `breakfast` `lunch` `dinner` `snack` `other`
- `confidence` 用 `measured`（有营养成分表）还是 `estimated`（目测）。
  **别把目测标成 measured** —— 助手会按这个字段决定结论说得多硬

---

## 写完之后

```bash
./scripts/hc status     # 数据抓到哪天了（不联网，秒回）
./scripts/hc doctor     # 格式坏了会在这里报出来
./scripts/hc summary --date 2026-01-01
```

**手写的 jsonl 格式坏了不会静默吞掉** —— `hc doctor` 会报。但反过来说，
写完了顺手跑一次 `doctor` 是值得的。

改了解析逻辑要重算历史数据，用 `hc rebuild`（离线，秒级），
**不要重新同步** —— 训练接口 30 秒/天限频，一年要跑 3 小时。
