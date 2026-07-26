---
name: xunji-sync
description: >
  从训记 App 同步训练、身体、饮食数据到本地。
  触发语：同步数据 / 拉一下训记 / 更新数据 / 从 App 导入 / 数据不全 /
  最近的训练没显示，以及 sync, xunji, 训记, import data, refresh data。
  凭证缺失或接口报错时也用这个 skill 排查。
license: MIT
---

# 训记数据同步

## 命令

```bash
./scripts/hc sync --since 30d          # 全部（训练+身体+饮食）
./scripts/hc sync train --since 90d    # 只同步训练
./scripts/hc sync body --since 1y      # 身体数据（范围查询，很快）
./scripts/hc rebuild                    # 用本地原始缓存离线重算，零网络请求
```

## 关于耗时，要提前告诉用户

**训练接口一天只能查一天，且限频 30 秒。**这不是可以优化掉的常数。

- 同步 30 天 ≈ 15 分钟
- 同步一年 ≈ 3 小时

工具会在开始前预告耗时。抓过的日期永不重抓（空日期也记录），
所以第二次跑同样范围是**零请求、瞬间完成**。

补历史数据建议分批：

```bash
./scripts/hc sync train --since 1y --budget-minutes 20
```

跑满 20 分钟就停，进度已保存，下次接着来。适合放进 cron 每小时跑一次。

随时 Ctrl-C 都是安全的。

## 改了解析逻辑之后

跑 `hc rebuild`，**不要**重新同步。原始响应都在 `data/training/raw/`，
离线重算一次网络请求都不用。重新同步一年要 3 小时。

## 凭证

四把 key 在 `.env`，分别对应训练、饮食记录、食物搜索、身体数据。
食物搜索是**独立的一把**，不要和饮食记录的混用。

**绝不要把 key 打印到对话、日志或报告里**，也不要让用户在聊天里粘贴 key。
需要排查时跑 `hc doctor -v`，它只显示脱敏后的片段。

## 常见错误

| 报错 | 处理 |
|---|---|
| `apikey missing` / `invalid` | 让用户回训记 App 重新申请，更新 `.env` |
| `仅VIP可用` | 该接口需要训记会员 |
| `too frequent` | 工具会自动等待重试，不用管 |
| 网络不可达 | 工具会退回到本地缓存，报告照常出，只是数据截止到上次同步 |

**没有凭证不是错误。**用户完全没配 key 时，手记模式功能一样完整，
见 `workout-log` skill。
