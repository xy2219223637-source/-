# 03 图像 PostgreSQL 定向结果

task_id: model-pricing-mvp-20261019-03-pg

2026-10-19 18:22–18:25 +08:00，仅在本轮独占临时schema、合成数据执行。固定应用源码为ticket-03最终acb1e711，测试前后源码摘要一致，无厂商或真实存储请求，运行业务schema版本前后2。

- 原批次pg-03-20261019-1822：13项图像行为全部通过；附带5项历史保护/并发回归中4通过，1项在fixture.setUp发放合成积分时建立数据库连接失败（server closed the connection unexpectedly）。该错误发生于测试行为前；完整日志保留，未改变应用或压制异常。该轮18个schema均清理，整轮结论仍记录为失败，见[原始结果](pg-image-result.json)。
- 针对缺失行为重跑pg-03-retry-20261019-1825：只选test_T11_T20_concurrent_members_cannot_overspend_last_pool，1/1通过，4.175秒；1个schema清理、源码摘要一致，见[定向复测](pg-image-legacy-retry.json)。不能将它描述成原批次18/18全绿。
- 可确认：图像折算/有效数量/规格/用量边界/幂等及账务行为通过；原批次未执行完成的历史并发行为另有通过证据。仅凭一次连接关闭不能确定数据库服务端根因，也不改数据库或启动任何重试守护。
- 独立源码审核未发现实质缺陷，另跑65项定向后端和10,000组Decimal验算通过，见[审核报告](review-03.md)。

此阶段证据不替代06完整当前代码MOCK报告，不授权真实调用，也不表示业务schema已迁移。
