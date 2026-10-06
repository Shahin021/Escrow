#!/usr/bin/env python3
"""Deploy ProjectEscrow through the Python SDK, bypassing the CLI's parser.

The CLI cannot carry a JSON string as a string (see prepare_deploy.py), so the
constructor arguments are built here, where a str stays a str.

DRY RUN BY DEFAULT. Without --submit this prints exactly what would be sent
and exits without touching the network. --submit requires GENLAYER_PRIVATE_KEY
and types the word 'deploy' at the prompt, so a submission cannot happen by
scrollback or by a stray flag.

    python scripts/deploy_escrow.py --milestones milestones.json \
        --worker 0x<40 hex> --max-revisions 1            # dry run
    python scripts/deploy_escrow.py ... --submit         # asks to confirm
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.prepare_deploy import check_address, validate_milestones  # noqa: E402

CONTRACT = "contracts/project_escrow.py"
NETWORK = "testnet_bradbury"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--milestones", required=True)
    parser.add_argument("--worker", required=True)
    parser.add_argument("--allowed-sources", default="raw.githubusercontent.com")
    parser.add_argument("--render-mode", default="text")
    parser.add_argument("--max-revisions", type=int, default=1)
    parser.add_argument("--continue-after-refund", action="store_true")
    parser.add_argument("--registry", default="")
    parser.add_argument("--submit", action="store_true")
    args = parser.parse_args()

    with open(args.milestones, encoding="utf-8") as handle:
        milestones, total_required = validate_milestones(handle.read())

    constructor_args = [
        check_address("worker", args.worker),
        json.dumps(milestones, separators=(",", ":")),
        args.allowed_sources,
        args.render_mode,
        args.max_revisions,
        args.continue_after_refund,
        check_address("registry", args.registry, allow_empty=True),
    ]

    print(f"contract        {CONTRACT}")
    print(f"network         {NETWORK}")
    print(f"total_required  {total_required}")
    print("arguments:")

    for index, value in enumerate(constructor_args):
        print(f"  [{index}] {type(value).__name__:5s} {value!r}")

    if not args.submit:
        print("\nDRY RUN: nothing was sent. Re-run with --submit to deploy.")
        return

    key = os.environ.get("GENLAYER_PRIVATE_KEY")

    if not key:
        print("\nGENLAYER_PRIVATE_KEY is not set; refusing to continue.")
        sys.exit(1)

    print("\nThis WILL send a deployment transaction to", NETWORK)

    if input("Type 'deploy' to continue: ").strip() != "deploy":
        print("Aborted; nothing was sent.")
        return

    from genlayer_py import create_account, create_client
    from genlayer_py.chains import testnet_bradbury
    from genlayer_py.types import TransactionStatus

    client = create_client(chain=testnet_bradbury, account=create_account(key))

    with open(CONTRACT, "r", encoding="utf-8") as handle:
        code = handle.read().encode("utf-8")

    tx = client.deploy_contract(code=code, args=constructor_args)

    print(f"transaction {tx}")

    receipt = client.wait_for_transaction_receipt(
        transaction_hash=tx, status=TransactionStatus.FINALIZED
    )

    print(json.dumps(receipt, indent=2, default=str)[:2000])


if __name__ == "__main__":
    main()
