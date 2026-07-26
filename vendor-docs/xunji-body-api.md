# 训记身体数据 Open API Skill

> **本文件是训记官方 Open API 文档的脱敏存档。**
> 原文里的 bearer token 已全部替换为环境变量名，真实值在仓库根目录的 `.env`（已 gitignore）。
> 调用时由 `src/health_assistant/xunji/client.py` 从环境变量读取，任何脚本和日志都不会打印 key。

## 原则
- 只在用户明确要求读取、导出、总结、对比、记录或更新身体数据时调用接口。
- 写入身体数据前，必须先给用户展示清晰的变更摘要，包括日期、指标类型、数值和单位，并等待用户明确确认。
- 用户确认前不要发送 `confirmed: true`。不要根据推测或未确认建议直接写入身体数据。
- 按日期范围和类型缓存读取结果；相同查询不要重复请求。
- 身体指标属于个人健康数据，分析趋势时保持谨慎，不做医疗诊断。

## 鉴权
- 请求头: `Authorization: Bearer $XUNJI_BODY_KEY`
- 也兼容请求头 `x-api-key`。
- 不支持把 Key 放在 body 或 query 里。
- 不要把 Key 写入日志或展示给第三方。

## 接口
- Base URL: `https://api.xunjiapp.cn`
- 查询身体数据: `POST /open/body/query_gzip`
- 写入身体数据: `POST /open/body/upsert_gzip`
- 成功时 `success === true`，核心数据在 `res`。

## 查询
```http
POST https://api.xunjiapp.cn/open/body/query_gzip
Authorization: Bearer $XUNJI_BODY_KEY
Content-Type: application/json

{
  "start_date": "2026-01-01",
  "end_date": "2026-06-28",
  "types": ["weight", "bodyfat"],
  "include_latest": true,
  "include_records": true,
  "limit": 500,
  "offset": 0
}
```

- 不传 `types` 时读取全部身体指标。只看体重用 `types: ["weight"]`，只看体脂率用 `types: ["bodyfat"]`。
- `records[]` 按日期倒序返回；每条有 `datestr`、`type`、`value`、`unit`、`label`、`label_en`。
- `latest` 是每个类型的最新记录；`by_type` 按类型归组本次返回的记录。
- `value` 会尽量转成数字。单位：`weight` 是 kg，`bodyfat` 是 %，围度/身体尺寸是 cm。

## 写入
- 写入按 `datestr + type` upsert：已有记录会更新，没有记录会新建。
- 先用 `dry_run: true` 校验；把 `res.summary` 展示给用户，并请用户确认。
- 只有用户确认后，才用相同记录再次请求 `dry_run: false` 和 `confirmed: true`。
- 真正写入必须带 `confirmed: true`；缺少时服务端会返回 `user confirmation required`。
```http
POST https://api.xunjiapp.cn/open/body/upsert_gzip
Authorization: Bearer $XUNJI_BODY_KEY
Content-Type: application/json

{
  "schema_version": "body_open_api_v1",
  "client_request_id": "unique-id-from-agent",
  "dry_run": true,
  "records": [
    { "datestr": "2026-06-28", "type": "weight", "value": 72.4 },
    { "datestr": "2026-06-28", "type": "bodyfat", "value": 18.2 }
  ]
}
```
```json
{
  "dry_run": false,
  "confirmed": true,
  "records": [
    { "datestr": "2026-06-28", "type": "weight", "value": 72.4 }
  ]
}
```

## 身体数据类型
- `weight`: 体重，单位 kg。
- `bodyfat`: 体脂率，单位 %。
- `neck`、`chest`、`weist`、`shoulder`、`bot`: 脖围、胸围、腰围、肩宽、臀围，单位 cm。腰围字段历史拼写是 `weist`，不要改成 `waist`。
- `arm_left`、`arm_right`、`forearm_left`、`forearm_right`、`leg_left`、`leg_right`、`cav_left`、`cav_right`: 左/右臂围、小臂围、腿围、小腿围，单位 cm。

## 限频与错误
- 身体数据查询和写入接口，同一 key 同一 endpoint 15 秒一次；`too frequent` 时等待 `retry_after_ms`。
- `apikey missing` / `apikey invalid`: 让用户回 App 重新申请，然后复制并重新发送最新的身体数据 Skill。
- `user confirmation required`: 先展示写入摘要给用户，等用户确认后再带 `confirmed: true` 重试。
- `仅VIP可用`: 当前账号需要会员权限。
