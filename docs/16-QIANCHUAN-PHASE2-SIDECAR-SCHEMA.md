# 千川全域推广 Phase 2：旁路数据骨架

更新时间：2026-09-23

## 已完成

- 新增 `qianchuan_delivery_plans`：保存本人计划草稿、平台镜像、素材链、凭证版本、状态、乐观锁版本及脱敏请求快照。
- 新增 `qianchuan_plan_operations`：保存 create/enable/pause/sync 操作、个人域幂等键、请求版本、确定性和平台诊断。
- 新增 `qianchuan_capability_probes`：能力结论绑定 owner、connection、binding、advertiser 和 credential_version，并具有失效时间。
- 新增显式迁移 `backend/scripts/migrate_qianchuan_delivery.py`。
- 新增独立 DTO、归属校验服务、目录能力服务和 `GET /api/qianchuan/plan-capabilities`。
- 应用启动时的通用 `create_all` 明确跳过三张旁路表，生产只能显式迁移。

## 数据库约束

- 非空 `(advertiser_id, platform_ad_id)` 唯一。
- `(tenant_id, created_by, idempotency_key)` 唯一。
- `budget_minor >= 0`，`operation_version >= 0`，`request_version >= 0`。
- capability conclusion 只能使用五种已定义结论。
- owner + asset、状态 + 更新时间、binding、operation + created_at 等查询均有独立索引。

## 开关行为

- 三开关默认关闭时，能力接口不调用平台，也不访问探测表。
- `PLAN_ENABLED` 是读取能力记录的前提。
- `PLAN_WRITE_ENABLED` 只有在读开关已开时才有效。
- `PLAN_WORKER_ENABLED` 只有在读、写开关都开时才有效。
- 创建能力还要求当前 credential_version 下的抖音号、商品和视频全域计划能力全部为 `verified`。
- 过期探测、旧凭证探测及 `no_sample_data` 均不能打开创建能力。

## 迁移安全

SQLite 测试已验证首次迁移自动备份、二次执行可重入、旧千川表行数不变、索引和唯一约束生效。

生产 PostgreSQL 尚未执行迁移。必须先完成外部备份，再显式运行：

```powershell
cd D:\project_work\video_cut\backend
.\.venv\Scripts\python.exe scripts\migrate_qianchuan_delivery.py --postgres-backup-confirmed
```

该参数只表示操作人已经完成备份，不会替代真实备份。回滚默认关闭功能开关并回退应用，保留三张审计表，不自动删表。

## 验证

```text
迁移专项测试：3 passed
能力门与归属测试：4 passed
全部千川回归：102 passed，166.427s
真实平台写请求：0
生产数据库 DDL：0
```

