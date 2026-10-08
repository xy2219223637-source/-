from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, _ensure_default_tenant_roles
from app.models import Tenant, TenantMember, TenantRole


class MaterialLibraryPermissionTests(unittest.TestCase):
    def test_existing_system_roles_gain_permission_without_changing_custom_roles(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine, tables=[Tenant.__table__, TenantRole.__table__, TenantMember.__table__])
        sessions = sessionmaker(bind=engine)
        with sessions() as db:
            db.add(Tenant(id="tenant", name="Tenant", code="tenant"))
            db.add_all([
                TenantRole(tenant_id="tenant", code="employee", name="普通员工", is_system=True, permissions=["dashboard.view"]),
                TenantRole(tenant_id="tenant", code="administrator", name="企业管理员", is_system=True, permissions=["dashboard.view"]),
                TenantRole(tenant_id="tenant", code="custom", name="自定义", is_system=False, permissions=["dashboard.view"]),
            ])
            db.commit()
        with patch("app.database.SessionLocal", sessions):
            _ensure_default_tenant_roles()
            _ensure_default_tenant_roles()
        with sessions() as db:
            roles = {row.code: row for row in db.query(TenantRole).all()}
            for code in ("employee", "administrator"):
                self.assertEqual(roles[code].permissions.count("material_library.access"), 1)
            self.assertEqual(roles["custom"].permissions, ["dashboard.view"])
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
