#!/usr/bin/env python3
"""
Bradbury runtime probes L1-L8 (V3 Phase 0). NON-PRODUCTION.

Run by the project owner with their own funded Bradbury key. The script only
RECORDS what it observes (tx hashes, receipts, raw return values, balances
over time). It never decides whether a probe "passed". Interpretation is done
afterwards, by a person, in probes/RESULTS.md.

Usage:
    export GENLAYER_PRIVATE_KEY=0x...        # never commit this
    python probes/run_bradbury_probes.py     # writes probes/results/bradbury-<UTC>.json

Optional environment:
    PROBE_UNIT_WEI       value unit for transfers (default 10**15 = 0.001 GEN)
    PROBE_EOA_RECIPIENT  L5 recipient (default: the owner's own address)
    PROBE_POLL_SECONDS   polling interval (default 15)
    PROBE_POLL_MINUTES   max polling per probe after finalization (default 20)
    PROBE_ONLY           comma list to run a subset, e.g. "L1,L7" (deploy always runs)
    PROBE_REUSE_A / PROBE_REUSE_B   reuse already-deployed probe addresses

Spend: 2 deploys + ~15 transactions + 3 units deposited (default 0.003 GEN),
of which 1 unit goes to PROBE_EOA_RECIPIENT and up to 1 unit may remain in
probe B. Bradbury testnet GEN only.
"""

import datetime as dt
import json
import os
import sys
import time
import traceback
from pathlib import Path

from genlayer_py import create_account, create_client
from genlayer_py.chains import testnet_bradbury
from genlayer_py.types import TransactionStatus

HERE = Path(__file__).resolve().parent
CONTRACT = HERE / "runtime_probe.py"
RESULTS_DIR = HERE / "results"

L6_URLS = {
    "github_redirect": "https://github.com/Shahin021/Escrow/raw/4fefef6a1d790a2fb39a62a937e76852c6cb778c/evidence/nova_valid.html",
    "raw_direct": "https://raw.githubusercontent.com/Shahin021/Escrow/4fefef6a1d790a2fb39a62a937e76852c6cb778c/evidence/nova_valid.html",
    "raw_404": "https://raw.githubusercontent.com/Shahin021/Escrow/4fefef6a1d790a2fb39a62a937e76852c6cb778c/evidence/does_not_exist.html",
}

UNIT = int(os.environ.get("PROBE_UNIT_WEI", str(10**15)))
POLL_S = int(os.environ.get("PROBE_POLL_SECONDS", "15"))
POLL_MIN = int(os.environ.get("PROBE_POLL_MINUTES", "20"))
ONLY = {x.strip() for x in os.environ.get("PROBE_ONLY", "").split(",") if x.strip()}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def jsonable(x):
    return json.loads(json.dumps(x, default=lambda o: o.hex() if isinstance(o, (bytes, bytearray)) else str(o)))


def describe(value):
    """Raw value plus its Python type tree, so decoding can be judged later."""
    def types(v):
        if isinstance(v, dict):
            return {"__type__": type(v).__name__, **{str(k): types(x) for k, x in v.items()}}
        if isinstance(v, (list, tuple)):
            return {"__type__": type(v).__name__, "items": [types(x) for x in v]}
        return type(v).__name__
    return {"value": jsonable(value), "python_types": types(value)}


class Recorder:
    def __init__(self, path: Path):
        self.path = path
        self.data = {"started_at": now(), "network": "testnet_bradbury", "unit_wei": str(UNIT), "steps": []}
        self.flush()

    def add(self, probe: str, step: str, **fields):
        entry = {"at": now(), "probe": probe, "step": step, **jsonable(fields)}
        self.data["steps"].append(entry)
        self.flush()
        print(f"[{entry['at']}] {probe} {step}: {json.dumps(jsonable(fields))[:300]}")
        return entry

    def flush(self):
        self.path.write_text(json.dumps(self.data, indent=2, sort_keys=True))


def contract_address_from(receipt) -> str:
    if "tx_data_decoded" in receipt and "contract_address" in receipt["tx_data_decoded"]:
        return receipt["tx_data_decoded"]["contract_address"]
    if "data" in receipt and "contract_address" in receipt["data"]:
        return receipt["data"]["contract_address"]
    raise ValueError("receipt has no contract_address")


def main() -> int:
    key = os.environ.get("GENLAYER_PRIVATE_KEY")
    if not key:
        print("Set GENLAYER_PRIVATE_KEY (a funded Bradbury testnet key). It is read from the environment only.")
        return 2

    RESULTS_DIR.mkdir(exist_ok=True)
    rec = Recorder(RESULTS_DIR / f"bradbury-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json")
    account = create_account(key)
    client = create_client(chain=testnet_bradbury, account=account)
    owner = account.address
    recipient = os.environ.get("PROBE_EOA_RECIPIENT", owner)
    rec.data["owner_address"] = owner
    rec.data["l5_recipient"] = recipient
    rec.flush()

    def want(p):
        return not ONLY or p in ONLY

    def wait(tx_hash, status, probe, label):
        t0 = time.time()
        try:
            r = client.wait_for_transaction_receipt(
                transaction_hash=tx_hash, status=status, interval=5000, retries=int(POLL_MIN * 60 / 5) + 60
            )
            rec.add(probe, f"{label}:{status.value}", tx=str(tx_hash), seconds=round(time.time() - t0, 1), receipt=r)
            return r
        except Exception as e:
            rec.add(probe, f"{label}:{status.value}:ERROR", tx=str(tx_hash), seconds=round(time.time() - t0, 1),
                    error=f"{type(e).__name__}: {e}")
            return None

    def write(probe, address, fn, args=None, value=0, final=True):
        try:
            h = client.write_contract(address=address, function_name=fn, args=args or [], value=value)
        except Exception as e:
            rec.add(probe, f"write {fn}:SUBMIT_ERROR", value=str(value), error=f"{type(e).__name__}: {e}",
                    trace=traceback.format_exc()[-1500:])
            return None
        rec.add(probe, f"write {fn}:submitted", tx=str(h), args=args or [], value=str(value))
        wait(h, TransactionStatus.ACCEPTED, probe, f"write {fn}")
        if final:
            wait(h, TransactionStatus.FINALIZED, probe, f"write {fn}")
        return h

    def read(probe, address, fn, args=None, label=None):
        try:
            v = client.read_contract(address=address, function_name=fn, args=args or [])
            rec.add(probe, f"read {label or fn}", **describe(v))
            return v
        except Exception as e:
            rec.add(probe, f"read {label or fn}:ERROR", error=f"{type(e).__name__}: {e}")
            return None

    def poll(probe, label, fn):
        """Record fn() every POLL_S seconds for up to POLL_MIN minutes, stopping early once it changes."""
        series, first = [], None
        deadline = time.time() + POLL_MIN * 60
        while True:
            try:
                v = fn()
            except Exception as e:
                v = f"ERROR {type(e).__name__}: {e}"
            series.append({"at": now(), "value": jsonable(v)})
            if first is None:
                first = v
            elif v != first:
                break
            if time.time() > deadline:
                break
            time.sleep(POLL_S)
        rec.add(probe, f"poll {label}", series=series)

    # ---- deploy A and B ----------------------------------------------------------
    code = CONTRACT.read_text()
    addrs = {}
    for name, env in (("A", "PROBE_REUSE_A"), ("B", "PROBE_REUSE_B")):
        if os.environ.get(env):
            addrs[name] = os.environ[env]
            rec.add("deploy", f"reuse {name}", address=addrs[name])
            continue
        h = client.deploy_contract(code=code, args=[])
        rec.add("deploy", f"deploy {name}:submitted", tx=str(h))
        r = wait(h, TransactionStatus.ACCEPTED, "deploy", f"deploy {name}")
        if r is None:
            print("Deployment did not reach ACCEPTED; see results file.")
            return 1
        addrs[name] = contract_address_from(r)
        rec.add("deploy", f"address {name}", address=addrs[name])
        wait(h, TransactionStatus.FINALIZED, "deploy", f"deploy {name}")
    A, B = addrs["A"], addrs["B"]
    rec.data["probe_A"], rec.data["probe_B"] = A, B
    rec.flush()

    # ---- L7: records -----------------------------------------------------------
    if want("L7"):
        write("L7", A, "store", [1, "hello"])
        read("L7", A, "load", [1])

    # ---- L1: view return shapes (Python side; JS side is l1_genlayer_js.mjs) ---------
    if want("L1"):
        for fn in ("probe_dict", "probe_list", "probe_typed_dict", "probe_typed_list", "probe_json"):
            read("L1", A, fn)

    # ---- L2: transaction datetime ---------------------------------------------------
    if want("L2"):
        for i in range(3):
            write("L2", A, "record_time", final=False)
            time.sleep(20)
        read("L2", A, "get_times")

    # ---- L3: value into paths that should reject it ------------------------------------
    if want("L3"):
        read("L3", A, "balance_now", label="balance_before")
        write("L3", A, "", value=1)                  # method-less (plain) transfer
        write("L3", A, "nonpayable_touch", value=1)  # value into a non-payable method
        read("L3", A, "balance_now", label="balance_after")
        read("L3", A, "deposits_total")

    # ---- funding for L4 / L5 -----------------------------------------------------------
    if want("L4") or want("L5"):
        write("fund", A, "deposit", value=3 * UNIT)
        read("fund", A, "balance_now", label="balance_after_deposit")
        read("fund", A, "deposits_total")

    # ---- L5: balance after emit_transfer to an EOA ----------------------------------------
    if want("L5"):
        read("L5", A, "balance_now", label="balance_before")
        write("L5", A, "send_to", [recipient, UNIT])
        poll("L5", "A.balance_now after FINALIZED",
             lambda: client.read_contract(address=A, function_name="balance_now", args=[]))

    # ---- L4: transfer into a contract without __receive__ -----------------------------------
    if want("L4"):
        read("L4", A, "balance_now", label="A_balance_before")
        read("L4", B, "balance_now", label="B_balance_before")
        write("L4", A, "send_to", [B, UNIT])
        poll("L4", "A.get_bounces after FINALIZED",
             lambda: client.read_contract(address=A, function_name="get_bounces", args=[]))
        read("L4", A, "get_bounces")
        read("L4", A, "bounced_total_str")
        read("L4", A, "balance_now", label="A_balance_after")
        read("L4", B, "balance_now", label="B_balance_after")

    # ---- L6: web.get redirect behaviour ----------------------------------------------------
    if want("L6"):
        for label, url in L6_URLS.items():
            rec.add("L6", f"url {label}", url=url)
            write("L6", A, "fetch", [url], final=False)
        read("L6", A, "get_fetches")

    # ---- L8: contract-to-contract message -----------------------------------------------------
    if want("L8"):
        write("L8", A, "ping", [B])
        poll("L8", "B.get_pings after FINALIZED",
             lambda: client.read_contract(address=B, function_name="get_pings", args=[]))
        read("L8", B, "get_pings")

    rec.data["finished_at"] = now()
    rec.flush()
    print(f"\nDone. Raw observations: {rec.path}")
    print(f"Probe A: {A}\nProbe B: {B}")
    print("Next: node probes/l1_genlayer_js.mjs " + A)
    return 0


if __name__ == "__main__":
    sys.exit(main())
