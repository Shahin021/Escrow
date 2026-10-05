# Phase 6 — security review, deployment prerequisites, delta record

Nothing in this document has been deployed. No Phase 3–6 contract has ever
run on Bradbury, and no transaction has been sent from this work.

## Freeze status

| Phase | Frozen at | Still accurate? |
| --- | --- | --- |
| V2 (`escrow_contract.py`) | `c0a03dc`, tag `v2-accepted` | **Yes.** Byte-identical ever since; the source guard checks it on every run. |
| Phase 2 | `1f9438b` | Yes for rubric, adjudication and consensus. |
| Phase 3 | `d9526e9` | **No longer describes the shipped contract.** |
| Phase 4 | merged in `f90a114` | Yes, except the interface id, now v2. |
| Phase 5 | merged in `11fbf3e` | Yes, plus the funding and interface changes on this branch. |

The Phase 3 freeze was reopened deliberately during this review. `fund()` was
payable and reverted on three caller-trippable conditions, which probe L9
shows would have kept the deposit while rolling back the accounting, and
would then have blocked every `confirm_outflow`, since that compares the
balance against the ledger. The fix split funding into a check-free payable
deposit, a non-payable `activate_funding()`, and
`withdraw_deposit_credit()`.

**Phase 3 is not re-frozen.** It should be re-frozen only after the
deployment rehearsal below, so the freeze describes code that has actually
run somewhere other than Direct Mode.

## Review log

Checked in this pass, with the result:

| Area | Result |
| --- | --- |
| Payable entry points vs L9 | `fund()` and `fund_appeal_credit()` each revert on exactly one condition, a zero-value call, which has nothing to strand. `__on_errored_message__` never raises, so returned value is always booked. |
| Outflow kinds | All ten are handled in `confirm_outflow` and authorized in `redirect_outflow` by their economic owner. |
| Expected balance | Includes every live bucket: locked, deposit credit, appeal credit, appeal bond, queued, bounced, unmatched. In-flight is excluded by design, being the amount leaving. |
| Accounting identity | `deposits_received + appeal_credit_received + unmatched_returns == deposit_credit_held + locked + appeal_credit_held + appeal_bond_held + queued_out + inflight_out + bounced_held + unmatched_held + sent_total`, asserted across the value-bearing suites. |
| Interface version | Bumped to `genlayer.milestone-escrow.v2`; the dApp enforces it and fails closed while unconfirmed. |
| Registry | Owner fixed at deployment, registration owner-only and single-use, reports idempotent per `(escrow, milestone, outcome, party)`. |

Defects found and fixed during the review: the `fund()` stranding and
confirmation-blocking defect; the dApp not enforcing the interface id; the
dApp treating an unread contract as supported.

## Deployment prerequisites

### Order matters

1. Deploy `contracts/escrow_registry.py`. The deploying account becomes the
   owner permanently; there is no transfer function.
2. Deploy `contracts/project_escrow.py` with the registry address, which is
   fixed at construction and has no setter.
3. Call `register_escrow(escrow_address)` **from the registry owner**, or
   every report the escrow emits is refused.

### Escrow constructor inputs

| Argument | Notes |
| --- | --- |
| `worker` | Must differ from the deploying client. |
| `milestones_json` | 1–10 milestones, each with `spec` and a decimal `amount`; optional `delivery_window_seconds` (1 … 31 536 000) and `required_check`. |
| `allowed_sources` | Must contain the evidence host, and `api.github.com` as well if any milestone sets `required_check`. |
| `render_mode` | `text`. |
| `max_revisions` | Revisions before `REJECTED_FINAL`. |
| `continue_after_refund` | `false` refunds the whole remaining principal when a milestone fails. |
| `registry` | Empty disables reporting. |

`parties()` publishes `total_required` before funding, so the exact deposit
is knowable up front. Funding is then two calls: `fund()` with the value,
then `activate_funding()`.

### Accounts and amounts

A client and a worker account, both funded for gas, plus the deposit. Appeal
bonds are 10% of a milestone, so a live scenario should use small amounts.

### Frontend

`VITE_ESCROW_ADDRESS` and optionally `VITE_REGISTRY_ADDRESS`; the app stays
read-only until the address is set and the contract reports interface v2.

## Open risk: the runtime that executes this code is not the one tested

The suite pins GenVM `v0.2.12` (`conftest.py`, `GENVM_VERSION`). The L9
probes on 2026-09-29 recorded Bradbury executing
`v0.2.11-x86_64-linux-release` (probes/RESULTS.md). So every test to date has
run against a **different runtime version** from the one that would execute
the deployed contract.

Nothing observed suggests a behavioural difference, but it is an untested
assumption and must be checked at deployment time: read the GenVM version
from the first deployment's trace and compare. A regression test keeps this
document and the pin in sync.

## Remaining before deployment

1. Localnet or glsim rehearsal of the full two-party scenario, including
   registry registration and reporting.
2. Confirm Bradbury's current GenVM version against the pin.
3. Re-freeze Phase 3 with a freeze commit describing the shipped funding
   flow.
4. Only then, deployment and the live scenario, on explicit approval.

## Delta record (to be completed after deployment)

| Item | Value |
| --- | --- |
| Source commit deployed | not deployed |
| Registry address | not deployed |
| Escrow address | not deployed |
| Deployment transaction hashes | not deployed |
| GenVM version observed | not deployed |
| Differences between deployed and source | not deployed |
