-- Frozen billing schema from baseline 3deddd8, SQLite fixtures only.
CREATE TABLE billing_audit_events (
	id VARCHAR(32) NOT NULL,
	tenant_id VARCHAR(64) NOT NULL,
	actor_type VARCHAR(20) NOT NULL,
	actor_id VARCHAR(64) NOT NULL,
	action VARCHAR(40) NOT NULL,
	resource_id VARCHAR(64) NOT NULL,
	trace_id VARCHAR(64) NOT NULL,
	reason VARCHAR(200) NOT NULL,
	changes_json JSON NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT
);

CREATE TABLE billing_idempotency (
	id VARCHAR(32) NOT NULL,
	operation VARCHAR(20) NOT NULL,
	tenant_id VARCHAR(64) NOT NULL,
	"key" VARCHAR(36) NOT NULL,
	request_digest VARCHAR(64) NOT NULL,
	actor_id VARCHAR(64) NOT NULL,
	response_json JSON NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (operation, tenant_id, "key"),
	FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT
);

CREATE TABLE billing_orders (
	id VARCHAR(32) NOT NULL,
	request_id VARCHAR(64) NOT NULL,
	tenant_id VARCHAR(64) NOT NULL,
	user_id VARCHAR(64) NOT NULL,
	quota_id VARCHAR(32) NOT NULL,
	period_key VARCHAR(7) NOT NULL,
	feature_code VARCHAR(100) NOT NULL,
	pricing_rule_id VARCHAR(32) NOT NULL,
	pricing_snapshot_json JSON NOT NULL,
	request_digest VARCHAR(64) NOT NULL,
	settlement_digest VARCHAR(64) NOT NULL,
	status VARCHAR(10) NOT NULL,
	execution_status VARCHAR(20) NOT NULL,
	reconciliation_status VARCHAR(20) NOT NULL,
	error_code VARCHAR(64) NOT NULL,
	reserved_microcredits BIGINT NOT NULL,
	charged_microcredits BIGINT NOT NULL,
	usage_json JSON NOT NULL,
	max_usage_json JSON NOT NULL,
	result_json JSON NOT NULL,
	created_at DATETIME NOT NULL,
	expires_at DATETIME NOT NULL,
	settled_at DATETIME,
	PRIMARY KEY (id),
	UNIQUE (tenant_id, id),
	FOREIGN KEY(tenant_id, user_id, period_key, quota_id) REFERENCES member_quotas (tenant_id, user_id, period_key, id) ON DELETE RESTRICT,
	CONSTRAINT ck_billing_order_state CHECK (status IN ('RESERVED','SETTLED','CANCELED')),
	CONSTRAINT ck_billing_order_amount CHECK (reserved_microcredits >= 0 AND charged_microcredits >= 0 AND charged_microcredits <= reserved_microcredits),
	UNIQUE (request_id),
	FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT,
	FOREIGN KEY(pricing_rule_id) REFERENCES billing_price_rules (id) ON DELETE RESTRICT
);

CREATE TABLE billing_price_rules (
	id VARCHAR(32) NOT NULL,
	pricing_scope_key VARCHAR(100) NOT NULL,
	feature_code VARCHAR(100) NOT NULL,
	version INTEGER NOT NULL,
	billing_mode VARCHAR(10) NOT NULL,
	status VARCHAR(10) NOT NULL,
	fixed_microcredits BIGINT,
	metric_rates_json JSON NOT NULL,
	created_by VARCHAR(64) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (pricing_scope_key, version),
	CONSTRAINT ck_billing_rule_state CHECK (version > 0 AND status IN ('DRAFT','ACTIVE','RETIRED')),
	CONSTRAINT ck_billing_rule_mode CHECK ((billing_mode = 'FIXED' AND fixed_microcredits IS NOT NULL AND fixed_microcredits > 0) OR (billing_mode = 'USAGE' AND fixed_microcredits IS NULL)),
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE TABLE billing_schema_versions (
	version INTEGER NOT NULL,
	checksum VARCHAR(64) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (version)
);

CREATE TABLE credit_accounts (
	id VARCHAR(32) NOT NULL,
	tenant_id VARCHAR(64) NOT NULL,
	balance_microcredits BIGINT NOT NULL,
	reserved_microcredits BIGINT NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (tenant_id, id),
	CONSTRAINT ck_credit_account_balance CHECK (balance_microcredits >= reserved_microcredits AND reserved_microcredits >= 0),
	UNIQUE (tenant_id),
	FOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT
);

CREATE TABLE credit_ledger (
	id VARCHAR(32) NOT NULL,
	tenant_id VARCHAR(64) NOT NULL,
	account_id VARCHAR(32) NOT NULL,
	order_id VARCHAR(32),
	event_type VARCHAR(10) NOT NULL,
	delta_microcredits BIGINT NOT NULL,
	balance_before_microcredits BIGINT NOT NULL,
	balance_after_microcredits BIGINT NOT NULL,
	business_key VARCHAR(160) NOT NULL,
	actor_type VARCHAR(20) NOT NULL,
	actor_id VARCHAR(64) NOT NULL,
	reason VARCHAR(200) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(tenant_id, account_id) REFERENCES credit_accounts (tenant_id, id) ON DELETE RESTRICT,
	FOREIGN KEY(tenant_id, order_id) REFERENCES billing_orders (tenant_id, id) ON DELETE RESTRICT,
	CONSTRAINT ck_credit_ledger_direction CHECK ((event_type = 'GRANT' AND delta_microcredits > 0 AND order_id IS NULL) OR (event_type = 'REVOKE' AND delta_microcredits < 0 AND order_id IS NULL) OR (event_type = 'CHARGE' AND delta_microcredits <= 0 AND order_id IS NOT NULL)),
	CONSTRAINT ck_credit_ledger_equation CHECK (balance_after_microcredits = balance_before_microcredits + delta_microcredits AND balance_before_microcredits >= 0 AND balance_after_microcredits >= 0),
	UNIQUE (order_id),
	UNIQUE (business_key)
);

CREATE TABLE member_quotas (
	id VARCHAR(32) NOT NULL,
	tenant_id VARCHAR(64) NOT NULL,
	user_id VARCHAR(64) NOT NULL,
	period_key VARCHAR(7) NOT NULL,
	period_type VARCHAR(10) NOT NULL,
	limit_microcredits BIGINT NOT NULL,
	used_microcredits BIGINT NOT NULL,
	reserved_microcredits BIGINT NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (tenant_id, user_id, period_key),
	UNIQUE (tenant_id, user_id, period_key, id),
	CONSTRAINT ck_member_quota_period CHECK (period_type = 'MONTHLY'),
	FOREIGN KEY(tenant_id, user_id) REFERENCES tenant_members (tenant_id, user_id) ON DELETE RESTRICT,
	CONSTRAINT ck_member_quota_balance CHECK (limit_microcredits >= used_microcredits + reserved_microcredits AND used_microcredits >= 0 AND reserved_microcredits >= 0)
);

CREATE INDEX ix_billing_audit_events_tenant_id ON billing_audit_events (tenant_id);

CREATE INDEX ix_billing_order_expiry ON billing_orders (status, expires_at);

CREATE INDEX ix_billing_order_owner ON billing_orders (tenant_id, user_id, created_at);

CREATE INDEX ix_credit_ledger_tenant_created ON credit_ledger (tenant_id, created_at);

CREATE UNIQUE INDEX uq_billing_active_scope ON billing_price_rules (pricing_scope_key) WHERE status = 'ACTIVE';

CREATE TRIGGER billing_immutable_billing_audit_events BEFORE UPDATE ON billing_audit_events BEGIN SELECT RAISE(ABORT, 'billing history is immutable'); END;

CREATE TRIGGER billing_immutable_billing_idempotency BEFORE UPDATE ON billing_idempotency BEGIN SELECT RAISE(ABORT, 'billing history is immutable'); END;

CREATE TRIGGER billing_immutable_credit_ledger BEFORE UPDATE ON credit_ledger BEGIN SELECT RAISE(ABORT, 'billing history is immutable'); END;

CREATE TRIGGER billing_no_delete_billing_audit_events BEFORE DELETE ON billing_audit_events BEGIN SELECT RAISE(ABORT, 'billing history is immutable'); END;

CREATE TRIGGER billing_no_delete_billing_idempotency BEFORE DELETE ON billing_idempotency BEGIN SELECT RAISE(ABORT, 'billing history is immutable'); END;

CREATE TRIGGER billing_no_delete_credit_ledger BEFORE DELETE ON credit_ledger BEGIN SELECT RAISE(ABORT, 'billing history is immutable'); END;

CREATE TRIGGER billing_order_no_delete BEFORE DELETE ON billing_orders BEGIN SELECT RAISE(ABORT, 'billing snapshot is immutable'); END;

CREATE TRIGGER billing_order_snapshot BEFORE UPDATE ON billing_orders WHEN NEW.id IS NOT OLD.id OR NEW.request_id IS NOT OLD.request_id OR NEW.tenant_id IS NOT OLD.tenant_id OR NEW.user_id IS NOT OLD.user_id OR NEW.quota_id IS NOT OLD.quota_id OR NEW.period_key IS NOT OLD.period_key OR NEW.feature_code IS NOT OLD.feature_code OR NEW.pricing_rule_id IS NOT OLD.pricing_rule_id OR NEW.pricing_snapshot_json IS NOT OLD.pricing_snapshot_json OR NEW.request_digest IS NOT OLD.request_digest OR NEW.reserved_microcredits IS NOT OLD.reserved_microcredits OR NEW.max_usage_json IS NOT OLD.max_usage_json OR NEW.created_at IS NOT OLD.created_at OR NEW.expires_at IS NOT OLD.expires_at OR OLD.status IN ('SETTLED','CANCELED') BEGIN SELECT RAISE(ABORT, 'billing snapshot is immutable'); END;

CREATE TRIGGER billing_price_no_delete BEFORE DELETE ON billing_price_rules BEGIN SELECT RAISE(ABORT, 'billing snapshot is immutable'); END;

CREATE TRIGGER billing_price_snapshot BEFORE UPDATE ON billing_price_rules WHEN NEW.id IS NOT OLD.id OR NEW.pricing_scope_key IS NOT OLD.pricing_scope_key OR NEW.feature_code IS NOT OLD.feature_code OR NEW.version IS NOT OLD.version OR NEW.billing_mode IS NOT OLD.billing_mode OR NEW.fixed_microcredits IS NOT OLD.fixed_microcredits OR NEW.metric_rates_json IS NOT OLD.metric_rates_json OR NEW.created_by IS NOT OLD.created_by OR NEW.created_at IS NOT OLD.created_at OR NOT ((OLD.status='DRAFT' AND NEW.status='ACTIVE') OR (OLD.status='ACTIVE' AND NEW.status='RETIRED')) BEGIN SELECT RAISE(ABORT, 'billing snapshot is immutable'); END;
