# 训记饮食数据 Open API Skill

> **本文件是训记官方 Open API 文档的脱敏存档。**
> 原文里的 bearer token 已全部替换为环境变量名，真实值在仓库根目录的 `.env`（已 gitignore）。
> 调用时由 `src/health_assistant/xunji/client.py` 从环境变量读取，任何脚本和日志都不会打印 key。

## 原则
- 只在用户明确要求读取、搜索、整理或写回饮食数据时调用接口。
- 写回、创建自定义食物或套用模板前，必须先展示变更摘要，并等待用户确认。
- 按查询条件缓存读取结果；相同条件不要重复请求。
- 查询范围默认限制在过去一年到未来 3 个月。

## 鉴权
- 查询、写回、自定义食物和模板接口请求头: `Authorization: Bearer $XUNJI_FOOD_KEY`
- 食物搜索接口请求头: `Authorization: Bearer $XUNJI_FOOD_SEARCH_KEY`
- 饮食记录接口也兼容请求头 `x-api-key`；食物搜索接口也兼容 `x-agent-key` 或 `x-api-key`。
- 不支持把 Key 放在 body 或 query 里。
- 不要把 Key 写入日志或展示给第三方。

## 接口
- 饮食记录 Base URL: `https://eatings.xunjiapp.cn`
- 查询饮食记录: `POST /open/food/query_gzip`
- 写回饮食记录: `POST /open/food/upsert_gzip`
- 新增或更新自定义食物: `POST /open/food/custom/upsert_gzip`
- 查询饮食模板: `POST /open/food/templates/list_gzip`
- 套用饮食模板: `POST /open/food/templates/apply_gzip`
- 食物搜索 Base URL: `https://api.xunjiapp.cn`
- 搜索官方食物: `POST /open_agent/food/search_gzip`
- 成功时 `success === true`，核心数据在 `res`。

## 查询饮食记录
```http
POST https://eatings.xunjiapp.cn/open/food/query_gzip
Authorization: Bearer $XUNJI_FOOD_KEY
Content-Type: application/json

{
  "start_date": "2025-06-12",
  "end_date": "2026-09-12",
  "include_detail": true
}
```

- 查询日期不要早于过去一年，也不要晚于未来 3 个月；用户要求更大范围时先解释限制并拆分到允许范围内。
- 读取后按日期、餐次、食物名称和记录 id 缓存；写回成功后用服务端返回数据覆盖缓存。
- 只读取用户明确需要的日期范围；不要为了模糊问题一次性扫全量。

## 搜索食物
```http
POST https://api.xunjiapp.cn/open_agent/food/search_gzip
Authorization: Bearer $XUNJI_FOOD_SEARCH_KEY
Content-Type: application/json

{
  "keyword": "鸡蛋",
  "limit": 8
}
```

- 搜索接口走主服务器，链路与 App 客户端一致：先按关键词找官方食物 id，再返回训记库里的 `ntr`、`units`、`uniquekey`。
- 优先使用 `res.foods`；其中 `ntr` 是每 100g 营养，`units` 是可选单位换算，`uniquekey` 写回时要带上。
- `res.d` 是客户端同款压缩数组，格式为 `[id, name, cal, carb, fat, protein, foodpic, uniquekey, units]`。
- 不确定食物匹配、单位或份量时先让用户确认；不要只凭相似名称直接写回。
- 搜索不到或用户要记录包装食品、餐厅食物、私有食物时，再通过公开营养信息或用户提供信息创建自定义食物。

## 创建自定义食物
```http
POST https://eatings.xunjiapp.cn/open/food/custom/upsert_gzip
Authorization: Bearer $XUNJI_FOOD_KEY
Content-Type: application/json

{
  "client_request_id": "unique-id-from-agent",
  "dry_run": false,
  "food": {
    "name": "用户确认的食物名",
    "ntr": {
      "cal": 165,
      "protein": 31,
      "fat": 3.6,
      "carb": 0,
      "foodpic": "",
      "foodUnit": [{ "unit": "份", "count": "1", "gram": 100 }]
    },
    "units": [{ "unit": "份", "count": "1", "gram": 100 }]
  }
}
```

- 只有搜索不到合适官方食物，或用户明确要创建私有食物时，才创建自定义食物。
- 需要新食物时，agent 应自行通过公开网页、包装营养成分表或用户提供信息查找每 100g 营养。
- 创建前必须向用户展示营养来源和摘要，并让用户确认食物名、每 100g 热量、蛋白质、脂肪、碳水；不确定时追问，不要估算。
- `ntr` 按训记格式写入：`cal`、`protein`、`fat`、`carb` 都是每 100g 数值；可带 `foodpic`。
- `units` 和 `ntr.foodUnit` 必须一致；没有明确份量单位时传空数组，默认按克记录。
- 创建成功后，使用返回的 `res.food` 里的 `name`、`uniquekey`、`unit/units`、`ntr` 再调用写回饮食记录。

## 写回饮食记录
```http
POST https://eatings.xunjiapp.cn/open/food/upsert_gzip
Authorization: Bearer $XUNJI_FOOD_KEY
Content-Type: application/json

{
  "client_request_id": "unique-id-from-agent",
  "dry_run": false,
  "foods": [
    {
      "date": "2026-06-12",
      "meal_type": "lunch",
      "name": "鸡胸肉",
      "amount": 150,
      "unit": "g",
      "uniquekey": "使用搜索结果或自定义食物返回的 uniquekey",
      "ntr": { "cal": 165, "protein": 31, "fat": 3.6, "carb": 0 }
    }
  ]
}
```

- 官方食物写回前先调用搜索接口，优先带上搜索结果里的 `uniquekey`、`units` 和 `ntr`。
- 写回前必须已经有用户确认过的 `ntr`；写回接口不会搜索食物或替你猜营养。
- 如果需要新增食物，先自行查找公开营养信息并创建自定义食物，再用创建接口返回的 `res.food` 作为写回来源。

## 自定义食物与模板
- 新增或更新自定义食物使用 `POST /open/food/custom/upsert_gzip`。
- 查询模板使用 `POST /open/food/templates/list_gzip`。
- 套用模板使用 `POST /open/food/templates/apply_gzip`。
- 创建自定义食物或套用模板也属于写回操作，必须先给用户看摘要并等待确认。

## 写回规则
- 写回前先展示将新增、修改或覆盖的日期、餐次、食物、数量和单位。
- 用户确认后再调用写回、自定义食物或模板套用接口。
- 有服务端记录 id 时更新原记录；没有 id 时新建记录。
- 不要因为查询结果里缺少旧记录就删除旧记录，除非用户明确要求删除。
- 不确定餐次、数量、单位或食物匹配时先追问用户。

## 限频与错误
- 饮食记录同一用户同类接口 15 秒一次；食物搜索接口同样 15 秒一次；`too frequent` 时等待提示的 retry 时间。
- `apikey missing` / `apikey invalid`: 让用户回 App 重新申请，然后复制并重新发送最新的饮食数据 Skill。
- `仅VIP可用`: 当前账号需要会员权限。
