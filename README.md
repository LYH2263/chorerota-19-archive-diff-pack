# Chorerota · 家庭值日轮转

底座：成员+任务 → round-robin 生成周表 → 申请对调 → 确认改表。

| 服务 | 端口 |
| --- | --- |
| 前端 | 5100 |
| API | 10100 |

```bash
docker compose up --build
pytest backend/app/tests
```

种子含 clean/dirty。0-1 空桩：`streak_badge` / `skip_week` / `chore_photo`。

## 封存差分包

周状态机：`draft → ready → sealed →(解封)→ ready`。ready 周 `POST /api/weeks/{id}/seal`
落一份不可变格位差分包（`week_packs`，规范化字节 + sha256，只增不改，再封存追加新版本）；
封存期间生成 / 对调确认 / 撤销一律 `409 week_sealed`。解封后恢复写，已有排班重生成需
`force:true`；`GET /api/weeks/{id}/diff` 把看板现网与钉住的包对照，列出不一致格
（changed / missing_live / extra_live）。周列表、`GET /api/weeks/{id}/pack` 详情、
看板对照入口三路同钉同一封存瞬间数据；改家庭名等设置不解封、不改包。

模块：`seal_snapshot`（快照写入）/ `seal_guard`（写拒绝）/ `seal_diff`（对照投影）。
