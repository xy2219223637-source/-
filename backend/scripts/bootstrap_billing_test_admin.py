"""One-time identity bootstrap ONLY for the dedicated billing_mvp_test database."""
import argparse
import getpass
import os
import re
import uuid

from sqlalchemy import select, text

from app.auth import hash_password
from app.config import get_settings
from app.database import engine
from app.models import Tenant, User, TenantMember
from app.services.billing.migration import verify
from app.services.billing.service import BillingService


def main():
    parser = argparse.ArgumentParser(description="首次创建独立积分测试库的平台管理员；不发积分")
    parser.add_argument("--phone", required=True)
    parser.add_argument("--operator", required=True)
    parser.add_argument("--confirm-test-database", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    if not (args.confirm_test_database and settings.billing_enabled and settings.billing_test_isolation
            and engine.dialect.name == "postgresql" and engine.url.database == "billing_mvp_test"):
        parser.error("Only the dedicated billing_mvp_test PostgreSQL database with test isolation is allowed")
    if not re.fullmatch(r"1\d{10}",args.phone) or not 1 <= len(args.operator.strip()) <= 64:
        parser.error("Invalid phone/operator")
    password = os.environ.get("BILLING_BOOTSTRAP_PASSWORD") or getpass.getpass("Test administrator initial password: ")
    if len(password) < 12 or password == settings.auth_secret:
        parser.error("Use an independent test password of at least 12 characters")
    with engine.connect() as connection:
        verify(connection)
    service = BillingService(settings=settings)
    with service.transaction() as db:
        db.execute(text("SELECT pg_advisory_xact_lock(724913222)"))
        if db.scalar(select(User.id).where(User.is_platform_admin.is_(True))):
            parser.error("Platform administrator already exists; use the authenticated admin APIs")
        if db.scalar(select(User.id).where(User.phone == args.phone)):
            parser.error("Phone already exists; no existing account will be promoted")
        tenant_id, user_id = uuid.uuid4().hex, uuid.uuid4().hex
        db.add(Tenant(id=tenant_id, name="积分测试平台", code="billing-platform-test")); db.flush()
        db.add(User(id=user_id, email=args.phone+"@phone.local", phone=args.phone, name="积分测试管理员",
                    password_hash=hash_password(password), is_platform_admin=True)); db.flush()
        db.add(TenantMember(tenant_id=tenant_id,user_id=user_id,role="owner",status="active")); db.flush()
        service.audit(db,tenant_id,args.operator.strip(),"TEST_ADMIN_BOOTSTRAP",user_id,
                      {"platform_admin":True},"独立测试环境首次初始化",internal=True)
    print("Test platform administrator created; sign in and create enterprises through existing admin APIs.")


if __name__ == "__main__":
    main()
