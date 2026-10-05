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

**Phase 3 is re-frozen on this branch**, by the empty commit
`chore(v3): re-freeze phase 3 after the funding fix`, once the local
rehearsal below passed. The scope of that freeze is stated plainly in its
message: it rests on Direct Mode plus a glsim rehearsal, which is strictly
more evidence than the original Phase 3 freeze had, and still not a network
run. A Bradbury deployment remains outstanding and is not covered by it.

## Review log

Checked in this pass, with the result:

| Area | Result |
| --- | --- |
| Payable entry points vs L9 | `fund()` and `fund_appeal_credit()` each revert on exactly one condition, a zero-value call, which has nothing to strand. `__on_errored_message__` never raises, so returned value is always booked. |
| Outflow kinds | All ten are handled in `confirm_outflow` and authorized in `redirect_outflow` by their economic owner. |
| Expected balance | Includes every live bucket: locked, deposit credit, appeal credit, appeal bond, queued, bounced, unmatched. In-flight is excluded by design, being the amount leaving. |
| Accounting identity | `deposits_received + appeal_credit_received + unmatched_returns == deposit_credit_held + locked + appeal_credit_held + appeal_bond_held + queued_out + inflight_out + bounced_held + unmatched_held + sent_total`, asserted across the value-bearing suites. |
| Interface version | Bumped to `genlayer.milestone-escrow.v2`; the dApp enforces it and fails closed while unconfirmed. |
| Registry | Owner fixed at deployment, registration owner-only and single-use, reports idempotent per `(escrow, milestone, outcome, party)`, and a failing registry cannot roll back a payment. |
| Authorization matrix | Every public write was classified. Only `redirect_outflow` accepts a caller-supplied recipient, and it is authorized per outflow kind by that value's economic owner. Every other outflow recipient is `self.client` or `self.worker`. The permissionless calls (`fund`, `resolve`, `mark_review_stalled`, `expire_delivery`, `finalize_rejection`, `emit_next_outflow`, `confirm_outflow`, `sweep_unmatched`, `close_project`, `__on_errored_message__`) take no caller-controlled recipient or amount. |
| dApp fail-closed | Writes require configuration, a connected wallet, the right chain, and a contract that has been read and reports interface v2. |

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

## Local rehearsal, performed

`test_deployment_rehearsal.py` walks the runbook end to end in glsim, which
executes the real contracts and really delivers emitted messages:

1. Deploy the registry; deploy the escrow with the registry address; register
   the escrow from the registry owner.
2. Fund short, be refused activation, top up, activate.
3. Deliver, adjudicate, claim, confirm, and see the registry record
   `RELEASED` only after the value moved.
4. Reject finally, fund appeal credit, appeal with the bond, lose the appeal,
   forfeit the bond, finalize, refund the whole remaining principal, cancel
   the later milestone, and close.
5. Propose and accept a settlement, confirm both legs, close.

The accounting identity is asserted after every value-bearing step.

**What the rehearsal does not prove.** glsim is a simulator, not the network:
no validator consensus, no real timing, no finality, and no gas. Nothing here
substitutes for a Bradbury run.

## Open risk: the runtime that executes this code is not the one tested

The suite pins GenVM `v0.2.12` (`conftest.py`, `GENVM_VERSION`). The L9
probes on 2026-09-29 recorded Bradbury executing
`v0.2.11-x86_64-linux-release` (probes/RESULTS.md).

**Bradbury's current GenVM version is unknown.** The only evidence available
is those traces from 2026-09-29, and a version observed then is not evidence
of the version running now. The environment preparing this branch cannot
reach `rpc-bradbury.genlayer.com` (the egress proxy answers 403), so no fresh
observation can be made here, and the runtime cannot be reproduced locally
either: the pinned bundle is the only one the harness installs.

So two things are true and neither should be overstated: every test to date
ran on `v0.2.12`, and the version that will execute a deployed contract has
not been established. It must be read from the first deployment's trace and
compared before the live scenario is trusted. A regression test keeps this
document and the pin in sync.

## Remaining before deployment

1. ~~Local rehearsal~~ — done, see above.
2. ~~Re-freeze Phase 3~~ — done, on simulated evidence; see the freeze status.
3. Establish Bradbury's current GenVM version. This cannot be done from the
   environment preparing this branch.
4. Deployment and the live scenario, on explicit approval, which is the only
   remaining gate.

## Delta record (to be completed after deployment)

| Item | Value |
| --- | --- |
| Source commit deployed | not deployed |
| Registry address | not deployed |
| Escrow address | not deployed |
| Deployment transaction hashes | not deployed |
| GenVM version observed | not deployed |
| Differences between deployed and source | not deployed |
