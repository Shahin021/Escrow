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
import requests

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
    decoded = receipt.get("tx_data_decoded")
    if isinstance(decoded, dict):
        address = decoded.get("contract_address")
        if address:
            return address

    data = receipt.get("data")
    if isinstance(data, dict):
        address = data.get("contract_address")
        if address:
            return address

    recipient = receipt.get("recipient")
    if isinstance(recipient, str) and recipient.startswith("0x") and len(recipient) == 42:
        return recipient

    raise ValueError("receipt has no contract_address or deployment recipient")


def main() -> int:
    key = os.environ.get("GENLAYER_PRIVATE_KEY")
    if not key:
        print("Set GENLAYER_PRIVATE_KEY (a funded Bradbury testnet key). It is read from the environment only.")
        return 2

    RESULTS_DIR.mkdir(exist_ok=True)
    rec = Recorder(RESULTS_DIR / f"bradbury-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json")
    account = create_account(key)
    client = create_client(chain=testnet_bradbury, account=account)

    # Bradbury currently returns gen_call as:
    #   {"result": {"data": "<calldata hex>", "status": {...}, ...}}
    # genlayer-py 0.16.3 expects:
    #   {"result": "<calldata hex>"}
    #
    # Adapt only gen_call responses, while keeping the SDK's own calldata
    # decoder and all other provider behaviour unchanged.
    _provider_make_request = client.provider.make_request

    def _compat_make_request(method, params):
        response = _provider_make_request(method=method, params=params)

        if method != "gen_call" or not isinstance(response, dict):
            return response

        result = response.get("result")
        if not isinstance(result, dict):
            return response

        status = result.get("status")
        if isinstance(status, dict):
            code = status.get("code", 0)
            if code not in (0, "0", None):
                raise RuntimeError(
                    f"gen_call failed: code={code}, "
                    f"message={status.get('message')}, "
                    f"stderr={result.get('stderr', '')}"
                )

        data = result.get("data")
        if not isinstance(data, str):
            raise RuntimeError(
                f"gen_call result missing string data: {result!r}"
            )

        adapted = dict(response)
        adapted["result"] = data
        return adapted

    client.provider.make_request = _compat_make_request

    owner = account.address
    recipient = os.environ.get("PROBE_EOA_RECIPIENT", owner)
    rec.data["owner_address"] = owner
    rec.data["l5_recipient"] = recipient
    rec.flush()

    def want(p):
        return not ONLY or p in ONLY

    def wait(tx_hash, status, probe, label):
        t0 = time.time()
        rpc_url = "https://rpc-bradbury.genlayer.com"
        wanted = status.value.upper()

        # Bradbury finalization can take a while, but every individual HTTP
        # request must have a bounded timeout so the runner cannot hang.
        max_seconds = 45 * 60 if wanted == "FINALIZED" else 5 * 60
        deadline = time.time() + max_seconds
        last_seen = None
        last_report = 0.0
        last_error = None

        acceptable = {
            "ACCEPTED": {"ACCEPTED", "FINALIZED"},
            "FINALIZED": {"FINALIZED"},
        }[wanted]

        while time.time() < deadline:
            try:
                status_resp = requests.post(
                    rpc_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "gen_getTransactionStatus",
                        "params": [{"txId": str(tx_hash)}],
                    },
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "genlayer-py",
                    },
                    timeout=(5, 15),
                )
                status_resp.raise_for_status()
                payload = status_resp.json()

                if payload.get("error"):
                    raise RuntimeError(f"status RPC error: {payload['error']}")

                info = payload.get("result") or {}
                seen = str(info.get("status") or "").upper()

                now_ts = time.time()
                if seen != last_seen or now_ts - last_report >= 30:
                    print(
                        f"[{now()}] {probe} {label}: waiting for {wanted}; "
                        f"network_status={seen or 'UNKNOWN'}"
                    )
                    last_seen = seen
                    last_report = now_ts

                if seen in {"CANCELED", "CANCELLED", "UNDETERMINED"}:
                    rec.add(
                        probe,
                        f"{label}:{wanted}:ERROR",
                        tx=str(tx_hash),
                        seconds=round(time.time() - t0, 1),
                        error=f"terminal network status: {seen}",
                        status_response=info,
                    )
                    return None

                if seen in acceptable:
                    receipt_resp = requests.post(
                        rpc_url,
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "gen_getTransactionReceipt",
                            "params": [{"txId": str(tx_hash)}],
                        },
                        headers={
                        "Content-Type": "application/json",
                        "User-Agent": "genlayer-py",
                    },
                    timeout=(5, 15),
                    )
                    receipt_resp.raise_for_status()
                    receipt_payload = receipt_resp.json()

                    if receipt_payload.get("error"):
                        raise RuntimeError(
                            f"receipt RPC error: {receipt_payload['error']}"
                        )

                    receipt = receipt_payload.get("result")
                    if receipt is None:
                        raise RuntimeError("receipt RPC returned null")

                    rec.add(
                        probe,
                        f"{label}:{wanted}",
                        tx=str(tx_hash),
                        seconds=round(time.time() - t0, 1),
                        receipt=receipt,
                    )
                    return receipt

                last_error = None

            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                now_ts = time.time()
                if now_ts - last_report >= 30:
                    print(
                        f"[{now()}] {probe} {label}: transient RPC error: "
                        f"{last_error}"
                    )
                    last_report = now_ts

            time.sleep(5)

        rec.add(
            probe,
            f"{label}:{wanted}:ERROR",
            tx=str(tx_hash),
            seconds=round(time.time() - t0, 1),
            error=f"timeout waiting for {wanted}; last_error={last_error}",
        )
        return None

    def write(probe, address, fn, args=None, value=0, final=True):
        try:
            h = client.write_contract(
                address=address,
                function_name=fn,
                args=args or [],
                value=value,
            )
        except Exception as e:
            rec.add(
                probe,
                f"write {fn}:SUBMIT_ERROR",
                value=str(value),
                error=f"{type(e).__name__}: {e}",
                trace=traceback.format_exc()[-1500:],
            )
            return None

        rec.add(
            probe,
            f"write {fn}:submitted",
            tx=str(h),
            args=args or [],
            value=str(value),
        )

        accepted = wait(
            h,
            TransactionStatus.ACCEPTED,
            probe,
            f"write {fn}",
        )
        if accepted is None:
            rec.add(
                probe,
                f"write {fn}:NOT_ACCEPTED",
                tx=str(h),
            )
            return None

        if final:
            finalized = wait(
                h,
                TransactionStatus.FINALIZED,
                probe,
                f"write {fn}",
            )
            if finalized is None:
                rec.add(
                    probe,
                    f"write {fn}:NOT_FINALIZED",
                    tx=str(h),
                )
                return None

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
        existing = read("L7", A, "load", [1], label="load_before_store")

        existing_obj = {}
        if isinstance(existing, str):
            try:
                parsed = json.loads(existing)
                if isinstance(parsed, dict):
                    existing_obj = parsed
            except Exception:
                pass

        if existing_obj.get("kv") == "hello":
            rec.add(
                "L7",
                "store skipped:already_present",
                value=existing,
                records=existing_obj.get("records"),
                last_i=existing_obj.get("last_i"),
                last_s=existing_obj.get("last_s"),
                last_amount=existing_obj.get("last_amount"),
            )
        else:
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

    # ---- L5: successful emit_transfer to an EOA, isolated on Probe B ------------------
    if want("L5"):
        stage = os.environ.get("PROBE_L5_STAGE", "fund").lower()

        if stage == "fund":
            read("L5", B, "balance_now", label="B_balance_before_fund")
            read("L5", B, "deposits_total", label="B_deposits_before_fund")

            if os.environ.get("PROBE_ARM_WRITE") != "YES":
                rec.add("L5", "stage1_write_guarded")
                print("L5 fund is READ-ONLY. Set PROBE_ARM_WRITE=YES to submit.")
                return 0

            print("L5 stage 1: funding Probe B; stopping after ACCEPTED.")
            h = write("L5", B, "deposit", value=2 * UNIT, final=False)
            if h is None:
                print("L5 funding was not accepted.")
                return 1

            rec.add(
                "L5",
                "stage1_funding_submitted",
                tx=str(h),
                note="Do not rerun fund stage. Check tx status later.",
            )
            print("L5 funding submitted.")
            return 0

        if stage == "send":
            read("L5", B, "balance_now", label="B_balance_before_send")
            read("L5", B, "deposits_total", label="B_deposits_before_send")

            if os.environ.get("PROBE_ARM_WRITE") != "YES":
                rec.add("L5", "stage2_write_guarded")
                print("L5 send is READ-ONLY. Set PROBE_ARM_WRITE=YES to submit.")
                return 0

            print("L5 stage 2: emitting transfer to EOA; stopping after ACCEPTED.")
            h = write("L5", B, "send_to", [recipient, UNIT], final=False)
            if h is None:
                print("L5 transfer was not accepted.")
                return 1

            rec.add(
                "L5",
                "stage2_transfer_submitted",
                tx=str(h),
                note="Do not rerun send stage. Check tx later and compare native balance.",
            )
            print("L5 transfer submitted.")
            return 0

        if stage == "read":
            read("L5", B, "balance_now", label="B_balance_after")
            read("L5", B, "deposits_total", label="B_deposits_after")
            return 0

        raise SystemExit(f"Unknown PROBE_L5_STAGE: {stage}")

    # ---- L4: transfer into a contract without __receive__ -----------------------------
    if want("L4"):
        deposited = read("L4", A, "deposits_total", label="deposits_total_before")

        try:
            deposited_i = int(deposited)
        except Exception:
            deposited_i = 0

        if deposited_i < 3 * UNIT:
            if os.environ.get("PROBE_ARM_WRITE") != "YES":
                rec.add("L4", "stage1_write_guarded")
                print("L4 funding is READ-ONLY. Set PROBE_ARM_WRITE=YES to submit.")
                return 0

            print("L4 stage 1: submitting funding; stopping after ACCEPTED.")
            h = write("L4", A, "deposit", value=3 * UNIT, final=False)
            if h is None:
                print("L4 funding was not accepted.")
                return 1

            rec.add(
                "L4",
                "stage1_funding_submitted",
                tx=str(h),
                note="Do not resubmit funding. Wait for this tx to finalize while running other probes.",
            )
            print("L4 funding submitted. Safe to work on another probe now.")
            return 0

        read("L4", A, "balance_now", label="A_balance_before")
        read("L4", B, "balance_now", label="B_balance_before")

        if os.environ.get("PROBE_ARM_WRITE") != "YES":
            rec.add("L4", "stage2_write_guarded")
            print("L4 transfer is READ-ONLY. Set PROBE_ARM_WRITE=YES to submit.")
            return 0

        print("L4 stage 2: submitting rejecting emit_transfer; stopping after ACCEPTED.")
        h = write("L4", A, "send_to", [B, UNIT], final=False)
        if h is None:
            print("L4 transfer was not accepted.")
            return 1

        rec.add(
            "L4",
            "stage2_transfer_submitted",
            tx=str(h),
            note="Do not rerun L4. Check this tx later, then read bounce state.",
        )
        print("L4 transfer submitted. Do not rerun L4 until we inspect its tx.")
        return 0

    # ---- L6: web.get redirect behaviour ----------------------------------------------------
    if want("L6"):
        for label, url in L6_URLS.items():
            rec.add("L6", f"url {label}", url=url)
            write("L6", A, "fetch", [url], final=False)
        read("L6", A, "get_fetches")

    # ---- L8: contract-to-contract message -----------------------------------------------------
    if want("L8"):
        read("L8", B, "get_pings", label="B_pings_before")

        if os.environ.get("PROBE_ARM_WRITE") != "YES":
            rec.add("L8", "ping_write_guarded")
            print("L8 ping is READ-ONLY. Set PROBE_ARM_WRITE=YES to submit.")
            return 0

        print("L8: submitting contract-to-contract ping; stopping after ACCEPTED.")
        h = write("L8", A, "ping", [B], final=False)
        if h is None:
            print("L8 ping was not accepted.")
            return 1

        rec.add(
            "L8",
            "ping_submitted",
            tx=str(h),
            note="Do not rerun L8. Check this tx later, then read B.get_pings.",
        )

        print("L8 ping submitted. Safe to continue other work.")
        return 0

    rec.data["finished_at"] = now()
    rec.flush()
    print(f"\nDone. Raw observations: {rec.path}")
    print(f"Probe A: {A}\nProbe B: {B}")
    print("Next: node probes/l1_genlayer_js.mjs " + A)
    return 0


if __name__ == "__main__":
    sys.exit(main())
