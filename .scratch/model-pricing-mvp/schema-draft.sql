-- PostgreSQL 规范草案；不是可直接部署的迁移脚本，严禁本轮执行。
-- 先核实目标数据库、schema、现有 v2 checksum、备份及恢复路径。
-- 正式迁移须由现有 migration.py 管理，升级 schema 版本/checksum，
-- 并重建 order snapshot trigger，将 model_price_id 纳入不可变字段。
-- 不改变旧账本/审计/幂等表的 append-only 保护，不删除旧 price_rules。

BEGIN;

CREATE TABLE billing_model_prices (
    model_key varchar(32) PRIMARY KEY CHECK (model_key ~ '^[0-9a-f]{32}$'),
    provider varchar(64) NOT NULL,
    model_id varchar(200) NOT NULL,
    current_pricing_json jsonb NOT NULL
        CHECK (jsonb_typeof(current_pricing_json) = 'object'),
    updated_by varchar(64) NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_billing_model_provider_id UNIQUE (provider, model_id)
);

ALTER TABLE billing_orders
    ADD COLUMN model_price_id varchar(32)
        REFERENCES billing_model_prices(model_key) ON DELETE RESTRICT,
    ADD COLUMN metering_json json,
    ADD COLUMN execution_mode varchar(6) NOT NULL DEFAULT 'LEGACY'
        CHECK (execution_mode IN ('LEGACY', 'MOCK', 'REAL'));

ALTER TABLE billing_orders ALTER COLUMN pricing_rule_id DROP NOT NULL;
ALTER TABLE billing_orders ADD CONSTRAINT ck_billing_order_price_source
    CHECK ((pricing_rule_id IS NOT NULL AND model_price_id IS NULL)
        OR (pricing_rule_id IS NULL AND model_price_id IS NOT NULL));

CREATE INDEX ix_billing_order_model_owner
    ON billing_orders (tenant_id, user_id, created_at DESC, id DESC)
    WHERE model_price_id IS NOT NULL;

-- 旧订单不回填：model_price_id 和 metering_json 继续 NULL。
-- 新真实模型订单 model_price_id 非空、pricing_rule_id 空。
-- 同模型 mock 验证订单亦 model_price_id 非空，execution_mode=MOCK；
-- 真实调用 execution_mode=REAL；历史旧订单保持 LEGACY，不作虚假回填。
-- 正式迁移将 execution_mode 和 model_price_id 一并纳入不可变快照字段。
-- pricing_snapshot_json 保存 {model_key,provider,model_id,configuration}；
-- configuration 严格对应 PricingInput，供旧请求结算使用。
-- metering_json 保存 {usage,supplier_cost,provider_request_id}，
-- 其中 UNKNOWN 成本字段为 null，不保存任何原始业务素材或凭据。
-- usage_json 保存新计算器需要的平坦可信计量值；不修改旧订单结构。
-- 必须在同次事务内通过正式迁移函数重建原 order snapshot trigger、
-- 计算实际结构 checksum 并写 schema version 3，不能只运行本 SQL。

COMMIT;
