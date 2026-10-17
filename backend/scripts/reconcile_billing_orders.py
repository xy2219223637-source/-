"""Offline trusted recovery; no arbitrary balance edits or model invocations."""
import argparse
import json
from app.services.billing.orders import BillingOrderService


def main():
    parser = argparse.ArgumentParser(description="扫描待核对订单，默认不自动释放；--recover 仅恢复可证明的状态")
    parser.add_argument("--recover", action="store_true")
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 1000:
        parser.error("batch size must be 1..1000")
    service = BillingOrderService()
    print(json.dumps({"scan": service.reconcile(recover=args.recover, batch_size=args.batch_size),
                      "accounting": service.check_accounts()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
