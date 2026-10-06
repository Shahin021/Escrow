#!/usr/bin/env python3
"""Read-only diagnosis of the Bradbury deployment failure.

Makes only these JSON-RPC calls: eth_chainId, eth_getCode,
eth_getTransactionCount, eth_getBlockByNumber and eth_estimateGas. It never
signs, never broadcasts, and never needs a private key: pass --sender with
the address you would deploy from.

It answers the questions that distinguish the candidate causes:

  config   does the consensus address the SDK targets actually have code,
           and does the node's chain id match the SDK's?
  size     the same estimate is run for the escrow and for a far smaller
           contract through the identical path, so a failure that depends on
           size is visible as a difference between them
  fee      the estimate can be repeated with a value attached, so a revert
           that disappears with a deposit is visible as such
  revert   the raw JSON-RPC error is printed verbatim, and any revert data is
           decoded against both consensus ABIs the SDK ships, plus the
           standard Error(string) and Panic(uint256)

Example:

    python scripts/diagnose_deploy.py --sender 0xYourAddress
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ESCROW = "contracts/project_escrow.py"
CONTROL = "contracts/escrow_registry.py"

ERROR_STRING = "0x08c379a0"  # Error(string)
PANIC = "0x4e487b71"  # Panic(uint256)


def selector_table():
    """Every function and error selector in both bundled consensus ABIs."""
    import genlayer_py
    from eth_utils import keccak

    root = os.path.dirname(genlayer_py.__file__)
    table = {}

    for name in ("consensus_main_abi.json", "consensus_main_abi_v06.json"):
        path = os.path.join(root, "consensus", "abi", name)

        if not os.path.exists(path):
            continue

        for entry in json.load(open(path, encoding="utf-8")):
            if entry.get("type") not in ("function", "error"):
                continue

            types = ",".join(i["type"] for i in entry.get("inputs", []))
            signature = f"{entry['name']}({types})"
            selector = "0x" + keccak(text=signature).hex()[:8]
            table.setdefault(selector, []).append(f"{name}: {entry['type']} {signature}")

    return table


def decode_revert(data):
    """Name the revert if we can; otherwise say so plainly."""
    if not data or data in ("0x", None):
        return "no revert data returned (the node gave no reason)"

    text = data if isinstance(data, str) else str(data)
    selector = text[:10]

    if selector == ERROR_STRING:
        try:
            from eth_abi import decode

            (message,) = decode(["string"], bytes.fromhex(text[10:]))

            return f"Error(string): {message}"
        except Exception:
            return f"Error(string), undecodable payload {text[:80]}"

    if selector == PANIC:
        return f"Panic(uint256) code {text[10:]}"

    matches = selector_table().get(selector)

    if matches:
        return f"{selector} = " + " | ".join(matches)

    return (
        f"{selector} is NOT in either consensus ABI shipped with genlayer-py. "
        "That points at a deployed consensus contract this SDK version does "
        "not describe."
    )


def estimate(client, sender, code_path, value=0):
    """Build the deployment payload and ask the node to estimate it."""
    from genlayer_py.abi import calldata
    from genlayer_py.abi.transactions import serialize
    from genlayer_py.contracts.actions import _encode_add_transaction_data
    from genlayer_py.contracts.utils import make_calldata_object
    from web3.constants import ADDRESS_ZERO

    code = open(code_path, "rb").read()

    args = (
        [
            "0x" + "a1" * 20,
            json.dumps(
                [{"spec": "diagnostic only", "amount": "1"}],
                separators=(",", ":"),
            ),
            "raw.githubusercontent.com",
            "text",
            1,
            False,
            "",
        ]
        if code_path == ESCROW
        else []
    )

    payload = serialize(
        [code, calldata.encode(make_calldata_object(method=None, args=args)), False]
    )

    # The encoder reads sender_account.address, so a tiny stand-in carries
    # the address without any key material.
    class _SenderOnly:
        address = sender

    encoded = _encode_add_transaction_data(
        self=client,
        sender_account=_SenderOnly(),
        recipient=ADDRESS_ZERO,
        consensus_max_rotations=client.chain.default_consensus_max_rotations,
        data=payload,
    )

    transaction = {
        "from": sender,
        "to": client.chain.consensus_main_contract["address"],
        "data": encoded,
        "value": hex(value),
    }

    raw = client.provider.make_request("eth_estimateGas", params=[transaction])

    return {
        "source_bytes": len(code),
        "calldata_bytes": len(encoded) // 2,
        "value": value,
        "raw": raw,
    }


def report(label, result):
    print(f"\n--- {label}")
    print(f"  source {result['source_bytes']} bytes, "
          f"calldata {result['calldata_bytes']} bytes, value {result['value']}")
    print("  raw JSON-RPC response:")
    print("    " + json.dumps(result["raw"], default=str)[:1200])

    error = result["raw"].get("error")

    if not error:
        print(f"  ESTIMATE SUCCEEDED: gas = {result['raw'].get('result')}")

        return True

    print(f"  error.code    {error.get('code')}")
    print(f"  error.message {error.get('message')}")

    data = error.get("data")

    print(f"  error.data    {str(data)[:160]}")
    print(f"  decoded       {decode_revert(data if isinstance(data, str) else None)}")

    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sender", required=True, help="address only; no key")
    parser.add_argument("--rpc", default=None)
    parser.add_argument(
        "--with-value",
        type=int,
        default=None,
        help="repeat the escrow estimate with this much value attached, to "
        "test whether a missing fee deposit is the cause",
    )
    args = parser.parse_args()

    if not args.sender.startswith("0x") or len(args.sender) != 42:
        print("--sender must be a 0x address")
        sys.exit(1)

    from genlayer_py import create_client
    from genlayer_py.chains import testnet_bradbury

    client = create_client(chain=testnet_bradbury)

    print("Environment")
    print(f"  SDK chain id            {testnet_bradbury.id}")

    node_chain = client.provider.make_request("eth_chainId", params=[])
    print(f"  node eth_chainId        {node_chain.get('result')}")

    consensus = testnet_bradbury.consensus_main_contract["address"]
    code = client.provider.make_request("eth_getCode", params=[consensus, "latest"])
    code_len = len(code.get("result", "0x")) // 2

    print(f"  consensus address       {consensus}")
    print(f"  code at that address    {code_len} bytes"
          + ("   <-- EMPTY, the SDK is pointing at nothing" if code_len <= 1 else ""))

    ok_control = report("CONTROL: small contract (escrow_registry.py)",
                        estimate(client, args.sender, CONTROL))
    ok_escrow = report("SUBJECT: project_escrow.py",
                       estimate(client, args.sender, ESCROW))

    if args.with_value is not None:
        report(f"SUBJECT with value={args.with_value}",
               estimate(client, args.sender, ESCROW, value=args.with_value))

    print("\nReading")

    if ok_control and not ok_escrow:
        print("  The small contract estimates and the large one does not,")
        print("  through the identical path: consistent with a size limit.")
    elif not ok_control and not ok_escrow:
        print("  BOTH fail, so this is not about the escrow's size. Look at")
        print("  the decoded revert, the consensus address and the fee model.")
    elif ok_control and ok_escrow:
        print("  Both estimate successfully; the earlier failure was not")
        print("  reproduced by this path.")

    print("\nNothing was signed or broadcast.")


if __name__ == "__main__":
    main()
