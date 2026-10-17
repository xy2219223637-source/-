"""Explicit additive migration. No balance backfill and no startup DDL."""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint, inspect, select, text

from app.models.billing import BILLING_TABLES, BillingSchemaVersion

VERSION = 2
IMMUTABLE = ("credit_ledger", "billing_audit_events", "billing_idempotency")
PRICE_COLUMNS = ("id", "pricing_scope_key", "feature_code", "version", "billing_mode", "fixed_microcredits", "metric_rates_json", "created_by", "created_at")
ORDER_COLUMNS = ("id", "request_id", "tenant_id", "user_id", "quota_id", "period_key", "feature_code", "pricing_rule_id", "pricing_snapshot_json", "request_digest", "reserved_microcredits", "max_usage_json", "created_at", "expires_at")


def checksum(dialect, version=VERSION):
    # Constraint sets have no stable iteration order across Python processes.
    # A migration and a later app startup must nevertheless agree on this hash.
    structures = []
    for table in sorted(BILLING_TABLES, key=lambda t: t.name):
        constraints = []
        for constraint in table.constraints:
            if isinstance(constraint, CheckConstraint):
                constraints.append(["check", constraint.name, str(constraint.sqltext).replace("OR (event_type = 'REVOKE' AND delta_microcredits < 0 AND order_id IS NULL) ", "") if version == 1 else str(constraint.sqltext)])
            elif isinstance(constraint, UniqueConstraint):
                constraints.append(["unique", [c.name for c in constraint.columns]])
            elif isinstance(constraint, ForeignKeyConstraint):
                constraints.append(["foreign", [c.name for c in constraint.columns], [e.target_fullname for e in constraint.elements], constraint.ondelete])
        structures.append({"name": table.name,
            "columns": [[c.name,str(c.type.compile(dialect=dialect)),c.nullable,c.primary_key] for c in table.columns],
            "constraints": sorted(constraints,key=lambda c:json.dumps(c)),
            "indexes": sorted([[i.name,i.unique,[c.name for c in i.columns],str(i.dialect_options[dialect.name].get("where"))] for i in table.indexes])})
    canonical = {"tables":structures,"append_only":IMMUTABLE,"price_snapshot":PRICE_COLUMNS,"order_snapshot":ORDER_COLUMNS,"version":version}
    return hashlib.sha256(json.dumps(canonical,sort_keys=True,separators=(",",":")).encode()).hexdigest()


def verify(connection, version=VERSION):
    inspector = inspect(connection)
    for table in BILLING_TABLES:
        if not inspector.has_table(table.name):
            raise RuntimeError(f"Billing migration required: missing {table.name}")
        columns = {c["name"]: c for c in inspector.get_columns(table.name)}
        if set(columns) != set(table.columns.keys()):
            raise RuntimeError(f"Incompatible billing columns: {table.name}")
        for column in table.columns:
            actual = columns.get(column.name)
            if actual is None or actual["nullable"] != column.nullable or actual["type"]._type_affinity != column.type._type_affinity:
                raise RuntimeError(f"Incompatible billing column: {table.name}.{column.name}")
            if getattr(actual["type"], "length", None) != getattr(column.type, "length", None):
                raise RuntimeError(f"Incompatible billing column length: {table.name}.{column.name}")
            if str(actual["type"].compile(dialect=connection.dialect)).upper() != str(column.type.compile(dialect=connection.dialect)).upper():
                raise RuntimeError(f"Incompatible billing storage type: {table.name}.{column.name}")
        if set(inspector.get_pk_constraint(table.name)["constrained_columns"]) != {c.name for c in table.primary_key}:
            raise RuntimeError(f"Incompatible billing primary key: {table.name}")
        uniques = {tuple(c["column_names"]) for c in inspector.get_unique_constraints(table.name)}
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint) and tuple(c.name for c in constraint.columns) not in uniques:
                raise RuntimeError(f"Missing billing uniqueness: {table.name}")
            if isinstance(constraint, ForeignKeyConstraint):
                expected = (tuple(c.name for c in constraint.columns), tuple(e.target_fullname for e in constraint.elements))
                actual = {(tuple(f["constrained_columns"]), tuple(f["referred_table"]+"."+c for c in f["referred_columns"])) for f in inspector.get_foreign_keys(table.name)}
                if expected not in actual:
                    raise RuntimeError(f"Missing billing foreign key: {table.name}")
            if isinstance(constraint, CheckConstraint) and constraint.name not in {c["name"] for c in inspector.get_check_constraints(table.name)}:
                raise RuntimeError(f"Missing billing check: {table.name}")
        indexes = {i["name"]: i for i in inspector.get_indexes(table.name)}
        for index in table.indexes:
            if index.name not in indexes or indexes[index.name]["unique"] != index.unique or indexes[index.name]["column_names"] != [c.name for c in index.columns]:
                raise RuntimeError(f"Missing billing index: {index.name}")
            if index.name == "uq_billing_active_scope":
                import re
                predicate = indexes[index.name].get("dialect_options", {}).get(connection.dialect.name+"_where")
                normalized = re.sub(r'::\w+|[()"\s]', '', str(predicate))
                if normalized != "status='ACTIVE'":
                    raise RuntimeError("Incompatible billing ACTIVE index predicate")
    stored_checksum = connection.execute(select(BillingSchemaVersion.checksum).where(BillingSchemaVersion.version == version)).scalar_one_or_none()
    if stored_checksum != checksum(connection.dialect, version):
        raise RuntimeError("Billing schema version/checksum mismatch")
    if list(connection.execute(select(BillingSchemaVersion.version)).scalars()) != [version]:
        raise RuntimeError("Unsupported billing schema version")
    if connection.dialect.name == "postgresql":
        triggers = set(connection.execute(text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgrelid IN (SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema())")).scalars())
    else:
        triggers = set(connection.execute(text("SELECT name FROM sqlite_master WHERE type='trigger'")).scalars())
    required = {f"billing_immutable_{table}" for table in IMMUTABLE} | {"billing_price_snapshot", "billing_order_snapshot"}
    if connection.dialect.name == "sqlite":
        required |= {f"billing_no_delete_{table}" for table in IMMUTABLE} | {"billing_price_no_delete", "billing_order_no_delete"}
    if not required <= triggers:
        raise RuntimeError("Billing immutable ledger/audit/idempotency protection missing")


def migrate_billing(bind, *, backup_confirmed=False):
    if bind.dialect.name not in {"postgresql", "sqlite"}:
        raise RuntimeError("Billing supports PostgreSQL and SQLite only")
    if bind.dialect.name == "postgresql" and not backup_confirmed:
        raise RuntimeError("Confirm independent PostgreSQL backup before billing migration")
    backup_path = None
    if bind.dialect.name == "sqlite" and bind.url.database not in (None, "", ":memory:"):
        source = Path(bind.url.database).resolve()
        if source.exists():
            backup_path = str(source) + ".billing-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f") + ".bak"
            with closing(sqlite3.connect(str(source))) as src, closing(sqlite3.connect(backup_path)) as dest:
                src.backup(dest)
    with bind.begin() as connection:
        if bind.dialect.name == "postgresql":
            connection.execute(text("SELECT pg_advisory_xact_lock(724913220)"))
        else:
            connection.execute(text("PRAGMA foreign_keys=ON"))
            connection.execute(text("BEGIN IMMEDIATE"))
        existing = set(inspect(connection).get_table_names()) & {t.name for t in BILLING_TABLES}
        if existing:
            versions = list(connection.execute(select(BillingSchemaVersion.version)).scalars())
            if versions == [1]:
                verify(connection, version=1)
                upgrade_ledger(connection)
                connection.execute(BillingSchemaVersion.__table__.update().where(BillingSchemaVersion.version == 1).values(version=VERSION, checksum=checksum(connection.dialect)))
                verify(connection)
                return {"status": "upgraded", "version": VERSION, "backup_path": backup_path}
            verify(connection)
            return {"status": "already_current", "version": VERSION, "backup_path": backup_path}
        if not {"tenants", "users", "tenant_members"} <= set(inspect(connection).get_table_names()):
            raise RuntimeError("Initialize existing identity schema before billing migration")
        for table in BILLING_TABLES:
            table.create(connection)
        # Database-level append-only protection, independent of ORM callers.
        if bind.dialect.name == "postgresql":
            connection.execute(text("""CREATE OR REPLACE FUNCTION billing_reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN RAISE EXCEPTION 'billing history is immutable'; END $$"""))
            for table in IMMUTABLE:
                connection.execute(text(f"CREATE TRIGGER billing_immutable_{table} BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION billing_reject_mutation()"))
        else:
            for table in IMMUTABLE:
                connection.execute(text(f"CREATE TRIGGER billing_immutable_{table} BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT, 'billing history is immutable'); END"))
                connection.execute(text(f"CREATE TRIGGER billing_no_delete_{table} BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT, 'billing history is immutable'); END"))
        for table_name, label, columns in (("billing_price_rules", "price", PRICE_COLUMNS), ("billing_orders", "order", ORDER_COLUMNS)):
            comparison = " OR ".join(f"NEW.{c} IS DISTINCT FROM OLD.{c}" if bind.dialect.name == "postgresql" else f"NEW.{c} IS NOT OLD.{c}" for c in columns)
            # Price transitions are monotonic; order terminal states are immutable.
            transition = (" OR NOT ((OLD.status='DRAFT' AND NEW.status='ACTIVE') OR (OLD.status='ACTIVE' AND NEW.status='RETIRED'))"
                          if label == "price" else " OR OLD.status IN ('SETTLED','CANCELED')")
            if bind.dialect.name == "postgresql":
                # JSON uses JSONB conversion for structural comparison in PostgreSQL.
                comparison = comparison.replace("NEW.metric_rates_json IS DISTINCT FROM OLD.metric_rates_json", "NEW.metric_rates_json::jsonb IS DISTINCT FROM OLD.metric_rates_json::jsonb")
                for column in ("pricing_snapshot_json", "max_usage_json"):
                    comparison = comparison.replace(f"NEW.{column} IS DISTINCT FROM OLD.{column}", f"NEW.{column}::jsonb IS DISTINCT FROM OLD.{column}::jsonb")
                connection.execute(text(f"""CREATE OR REPLACE FUNCTION billing_protect_{label}() RETURNS trigger LANGUAGE plpgsql AS $$
                    BEGIN IF TG_OP='DELETE' THEN RAISE EXCEPTION 'billing snapshot is immutable'; END IF;
                    IF {comparison}{transition} THEN RAISE EXCEPTION 'billing snapshot is immutable'; END IF; RETURN NEW; END $$"""))
                connection.execute(text(f"CREATE TRIGGER billing_{label}_snapshot BEFORE UPDATE OR DELETE ON {table_name} FOR EACH ROW EXECUTE FUNCTION billing_protect_{label}()"))
            else:
                connection.execute(text(f"CREATE TRIGGER billing_{label}_snapshot BEFORE UPDATE ON {table_name} WHEN {comparison}{transition} BEGIN SELECT RAISE(ABORT, 'billing snapshot is immutable'); END"))
                connection.execute(text(f"CREATE TRIGGER billing_{label}_no_delete BEFORE DELETE ON {table_name} BEGIN SELECT RAISE(ABORT, 'billing snapshot is immutable'); END"))
        connection.execute(BillingSchemaVersion.__table__.insert().values(version=VERSION, checksum=checksum(connection.dialect)))
        # Only built-in roles are upgraded; never silently expand custom roles.
        from app.models import TenantRole
        from app.models.billing import BillingAuditEvent
        for role in connection.execute(select(TenantRole.__table__).where(TenantRole.is_system.is_(True), TenantRole.code.in_(["employee", "administrator"])) ).mappings():
            additions = {"billing.mock.use"}
            if role["code"] == "administrator":
                additions |= {"billing.view", "billing.quota.manage"}
            before = role["permissions"] or []
            after = sorted(set(before) | additions)
            if after != sorted(before):
                connection.execute(TenantRole.__table__.update().where(TenantRole.id == role["id"]).values(permissions=after))
                connection.execute(BillingAuditEvent.__table__.insert().values(tenant_id=role["tenant_id"], actor_type="SERVICE",
                    actor_id="billing-migration", action="ROLE_UPGRADE", resource_id=str(role["id"]), changes_json={"before":before,"after":after}))
        verify(connection)
    return {"status": "migrated", "version": VERSION, "backup_path": backup_path}


def upgrade_ledger(connection):
    """Expand ledger direction without changing any historical entry."""
    from sqlalchemy.schema import CreateTable
    from app.models.billing import CreditLedger
    table = CreditLedger.__table__
    direction = next(str(c.sqltext) for c in table.constraints if c.name == "ck_credit_ledger_direction")
    if connection.dialect.name == "postgresql":
        connection.execute(text("ALTER TABLE credit_ledger DROP CONSTRAINT ck_credit_ledger_direction"))
        connection.execute(text("ALTER TABLE credit_ledger ADD CONSTRAINT ck_credit_ledger_direction CHECK (" + direction + ")"))
    else:
        # No tables reference the ledger. Rebuild under the migration write lock;
        # copying is INSERT-only and existing append-only triggers stay effective
        # until the old table is dropped. SQLite DDL rolls back with this transaction.
        ddl = str(CreateTable(table).compile(dialect=connection.dialect)).replace("CREATE TABLE credit_ledger", "CREATE TABLE credit_ledger_v2", 1)
        connection.execute(text(ddl))
        columns = ", ".join(column.name for column in table.columns)
        connection.execute(text(f"INSERT INTO credit_ledger_v2 ({columns}) SELECT {columns} FROM credit_ledger"))
        connection.execute(text("DROP TABLE credit_ledger"))
        connection.execute(text("ALTER TABLE credit_ledger_v2 RENAME TO credit_ledger"))
        for index in table.indexes:
            index.create(connection)
        connection.execute(text("CREATE TRIGGER billing_immutable_credit_ledger BEFORE UPDATE ON credit_ledger BEGIN SELECT RAISE(ABORT, 'billing history is immutable'); END"))
        connection.execute(text("CREATE TRIGGER billing_no_delete_credit_ledger BEFORE DELETE ON credit_ledger BEGIN SELECT RAISE(ABORT, 'billing history is immutable'); END"))
