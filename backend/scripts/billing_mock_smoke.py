"""Three-role HTTP smoke test, explicitly restricted to the isolated Mock profile."""
import argparse
import json
import os
import secrets
import uuid

import httpx


def main():
    parser=argparse.ArgumentParser(description="通过真实HTTP验证独立积分测试环境；会创建新测试企业并通过API发放测试积分")
    parser.add_argument("--base-url", default="http://127.0.0.1:18000")
    parser.add_argument("--phone", required=True)
    parser.add_argument("--confirm-test-environment", action="store_true")
    args=parser.parse_args()
    if not args.confirm_test_environment or not os.environ.get("BILLING_SMOKE_ADMIN_PASSWORD"):
        parser.error("Explicit test confirmation and BILLING_SMOKE_ADMIN_PASSWORD are required")
    with httpx.Client(base_url=args.base_url.rstrip("/")+"/api/", timeout=20) as client:
        def call(method,path,token=None,payload=None,key=None,expected=200):
            headers={"Authorization":"Bearer "+token} if token else {}
            if key: headers["Idempotency-Key"]=key
            response=client.request(method,path,headers=headers,json=payload)
            if response.status_code != expected:
                # Do not echo credentials, full requests, or raw provider/database errors.
                raise RuntimeError(f"Smoke failed: {method} {path}: expected {expected}, got {response.status_code}")
            return response.json()
        def login(phone,password):
            return call("POST","auth/login",payload={"phone":phone,"password":password})
        admin_login=login(args.phone,os.environ["BILLING_SMOKE_ADMIN_PASSWORD"])
        admin=admin_login["access_token"]
        config=call("GET","me/billing/config",admin)
        if not (config["test_isolation"] and config["mock_enabled"] and admin_login["user"]["is_platform_admin"]=="true"):
            raise RuntimeError("Refusing to mutate an environment without isolated Mock billing and platform RBAC")
        definitions=[{"feature_code":"mock_fixed","billing_mode":"FIXED","fixed_credits":"20"},
                     {"feature_code":"mock_text","billing_mode":"USAGE","metrics":[
                         {"metric":"input_tokens","unit_size":1000,"credits_per_unit":"0.20"},
                         {"metric":"output_tokens","unit_size":1000,"credits_per_unit":"0.80"}]}]
        rules=call("GET","platform-admin/billing/price-rules?page_size=100",admin)["items"]
        for definition in definitions:
            active=next((r for r in rules if r["feature_code"]==definition["feature_code"] and r["status"]=="ACTIVE"),None)
            if active:
                matches=(active["fixed_microcredits"]==200000 if definition["billing_mode"]=="FIXED" else active["metrics"]==definition["metrics"])
                if not matches: raise RuntimeError("Existing active test prices differ; refusing to overwrite them")
            else:
                version=max((r["pricing_version"] for r in rules if r["feature_code"]==definition["feature_code"]),default=0)+1
                rule=call("POST","platform-admin/billing/price-rules",admin,{**definition,"version":version},expected=201)
                call("POST",f"platform-admin/billing/price-rules/{rule['id']}/activate",admin)
        def phone(): return "139"+f"{secrets.randbelow(10**8):08d}"
        owner_phone,initial=phone(),secrets.token_urlsafe(20)
        tenant=call("POST","platform-admin/tenants",admin,{"name":"积分HTTP验收企业","code":"billing-"+uuid.uuid4().hex[:12],
                    "owner_name":"测试企业管理员","owner_phone":owner_phone,"initial_password":initial})
        tenant_id=tenant["id"]
        owner=login(owner_phone,initial)["access_token"]
        owner_password=secrets.token_urlsafe(20)
        call("POST","auth/change-password",owner,{"current_password":initial,"new_password":owner_password})
        team=call("POST","tenant-admin/teams",owner,{"name":"积分测试团队"})
        roles=call("GET","tenant-admin/roles",owner)
        role=next(r for r in roles if r["code"]=="employee")
        members=[]
        for name in ("A","B"):
            member_phone,member_password=phone(),secrets.token_urlsafe(20)
            member=call("POST","tenant-admin/members",owner,{"name":"测试成员"+name,"phone":member_phone,
                        "initial_password":member_password,"team_id":team["id"],"role_id":role["id"]})
            members.append((member["user_id"],member_phone,member_password))
        grant_path=f"platform-admin/tenants/{tenant_id}/credits/grants"
        grant_key=str(uuid.uuid4())
        grant_body={"amount_credits":"10000","reason":"独立测试HTTP验收"}
        first=call("POST",grant_path,admin,grant_body,grant_key,201)
        if call("POST",grant_path,admin,grant_body,grant_key) != first: raise RuntimeError("Grant replay changed")
        call("POST",grant_path,admin,{**grant_body,"amount_credits":"1"},grant_key,409)
        for member,limit in zip(members,("4000","7000")):
            call("PUT",f"tenant-admin/billing/members/{member[0]}/quota",owner,{"total_limit_credits":limit,"reason":"本月总额度"})
        member=login(members[0][1],members[0][2])["access_token"]
        call("POST","auth/change-password",member,{"current_password":members[0][2],"new_password":secrets.token_urlsafe(20)})
        for kind in ("fixed","text"):
            key=str(uuid.uuid4())
            first=call("POST","ai/mock/"+kind,member,{"text":"测试"},key)
            if call("POST","ai/mock/"+kind,member,{"text":"测试"},key) != first: raise RuntimeError("Billing replay changed")
        call("POST","ai/mock/text",member,{"actual_usage":{"input_tokens":1}},str(uuid.uuid4()),422)
        call("POST",grant_path,member,grant_body,str(uuid.uuid4()),403)
        call("PUT",f"tenant-admin/billing/members/{admin_login['user']['id']}/quota",owner,{"total_limit_credits":"1","reason":"越权校验"},expected=404)
        call("POST","operations/billing/sync",member,{},expected=403)
        call("POST","internal/billing/settle",member,{},expected=403)
        balance=call("GET","tenant-admin/billing/credits",owner)
        quota=call("GET","me/billing/quota",member)
        ledger=call("GET","tenant-admin/billing/ledger",owner)
        usage=call("GET","tenant-admin/billing/members/usage",owner)
        other=next(r for r in usage["items"] if r["user_id"]==members[1][0])
        if (balance["balance_credits"],balance["reserved_credits"],quota["used_credits"],other["used_credits"],ledger["total"]) != ("9978.8000","0.0000","21.2000","0.0000",3):
            raise RuntimeError("Shared pool accounting mismatch")
        print(json.dumps({"ok":True,"tenant_id":tenant_id,"balance_credits":balance["balance_credits"],"member_used_credits":quota["used_credits"],"ledger_count":ledger["total"]}))


if __name__=="__main__":
    main()
