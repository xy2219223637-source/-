"""Same financial contracts on real PostgreSQL connections, each in a fresh schema."""
import os
import unittest
import uuid
from pathlib import Path
import subprocess

from sqlalchemy import create_engine
from sqlalchemy.schema import CreateSchema, DropSchema
import test_billing_management as management
import test_billing_orders as orders


class PostgresFixture:
    def make_engine(self):
        self.test_schema = "test_billing_" + uuid.uuid4().hex
        self.admin_engine = create_engine(os.environ["BILLING_TEST_DATABASE_URL"])
        with self.admin_engine.begin() as connection:
            connection.execute(CreateSchema(self.test_schema))
        self.addCleanup(self.drop_schema)
        return create_engine(os.environ["BILLING_TEST_DATABASE_URL"],
                             connect_args={"options": f"-csearch_path={self.test_schema} -clock_timeout=5000 -cstatement_timeout=15000"})

    def drop_schema(self):
        if not self.test_schema.startswith("test_billing_") or len(self.test_schema) != 45:
            raise RuntimeError("Refusing cleanup outside the generated billing test schema")
        with self.admin_engine.begin() as connection:
            connection.execute(DropSchema(self.test_schema, cascade=True))
        self.admin_engine.dispose()


@unittest.skipUnless(os.environ.get("BILLING_TEST_DATABASE_URL"), "Dedicated PostgreSQL test URL required")
class PostgresManagementTests(PostgresFixture, management.ManagementTests):
    pass


@unittest.skipUnless(os.environ.get("BILLING_TEST_DATABASE_URL"), "Dedicated PostgreSQL test URL required")
class PostgresOrderTests(PostgresFixture, orders.OrderTests):
    pass


@unittest.skipUnless(os.environ.get("BILLING_TEST_DATABASE_URL") and os.environ.get("BILLING_TEST_PG_BIN"), "Dedicated PostgreSQL tools required")
class PostgresBackupTests(PostgresFixture, management.BillingFixture):
    def test_pg_dump_restore_preserves_financial_history_and_triggers(self):
        from sqlalchemy.engine import make_url
        from sqlalchemy import select
        from app.models.billing import CreditAccount, CreditLedger
        from app.services.billing.migration import verify
        self.grant()
        url=make_url(os.environ["BILLING_TEST_DATABASE_URL"])
        env={**os.environ,"PGHOST":url.host,"PGPORT":str(url.port or 5432),"PGUSER":url.username,"PGDATABASE":url.database}
        if url.password: env["PGPASSWORD"]=url.password
        directory=Path(os.environ["BILLING_TEST_PG_BIN"])
        suffix=".exe" if os.name=="nt" else ""
        backup=Path(self.temp.name)/"billing.dump"
        subprocess.run([str(directory/("pg_dump"+suffix)),"--format=custom","--schema="+self.test_schema,"--file="+str(backup)],env=env,check=True,capture_output=True)
        self.assertTrue(backup.stat().st_size>0)
        self.engine.dispose()
        with self.admin_engine.begin() as connection:
            connection.execute(DropSchema(self.test_schema,cascade=True))
        subprocess.run([str(directory/("pg_restore"+suffix)),"--dbname="+url.database,"--no-owner","--no-acl",str(backup)],env=env,check=True,capture_output=True)
        with self.engine.connect() as connection:
            verify(connection)
            self.assertEqual(connection.execute(select(CreditAccount.balance_microcredits)).scalar_one(),10000000)
            self.assertEqual(len(connection.execute(select(CreditLedger.id)).all()),1)
