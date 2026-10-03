# Phase 4 — integrations

Three deliverables: a versioned read interface, a GitHub check gate bound to
the pinned commit, and an outcome registry fed by escrows.

Phases 1–3 are unchanged. No Phase 5 dApp work and no Phase 6 deployment or
final security review is included here.

## Composability interface

`ESCROW_INTERFACE_ID = "genlayer.milestone-escrow.v1"` plus `interface_id()`,
`is_milestone_released()`, `released_amount()` and `parties()`. Shapes are
frozen under that id; a change requires a new id.

`released_amount()` is zero unless the milestone reached `RELEASED`, so a
consumer cannot read an amount for work that was only approved or whose
payment is still in flight. All amounts are decimal strings, because probe L1
showed a JS consumer receives a Number for small integers and a string for
large ones.

**Limitation.** Probe L8 verified one-way emitted **write** messages between
contracts on Bradbury. A **synchronous cross-contract view read** is not
verified on Bradbury. It does work in glsim, which the registry tests use,
but that is a simulator, not the network. Until a live probe exists, treat
on-chain synchronous consumption of this interface as unverified; auditors,
the dApp and off-chain consumers are unaffected.

## GitHub check gate

Optional per-milestone `required_check`. The API URL is built from the
evidence URL's own owner, repo and 40-hex commit, so the check is always
resolved against the exact commit under review.

| Observed | Result |
| --- | --- |
| Every run with that exact name completed successfully | rubric decides as usual |
| A run with that name completed with any other conclusion | `REJECTED`, no model call, consumes a revision |
| No run with that name, or any still queued or in progress | `UNAVAILABLE`, no revision consumed |
| Non-200, including 403 and 429, or an unreachable host | `UNAVAILABLE`, no revision consumed |
| Malformed payload | the transaction reverts with no state change |

A green check never approves on its own. The observed runs are reduced to a
canonical sorted string and hashed with the check name and commit-bound URL
into `check_hash`, which is bound to consensus alongside `outcome`,
`approved`, `evidence_hash`, `criteria_bits`, `rubric_hash` and `excerpt`.

Constructor rules: the name is 1–100 characters from `[A-Za-z0-9 ._-]`, and
`api.github.com` must appear in `allowed_sources`.

**Operational limit.** GitHub's unauthenticated limit is 60 requests per hour
per IP, measured in Phase 0. Validators on shared infrastructure can exhaust
it; the result is `UNAVAILABLE`, which is safe but can delay a milestone.

## Registry

`contracts/escrow_registry.py` records terminal outcomes per address.

Authentication: a report is accepted only from an escrow the registry owner
registered. Any contract can emit a call claiming an outcome, so the sender
address alone proves nothing; registration is what grants write access.
Registration is owner-only and single-use. Counters only increase and the
report log is append-only.

**This is a record, not a trust score.** Addresses are free, so a party can
create fresh ones or transact with itself. Consumers should read it as
history.

### Test path

Direct Mode executes no inter-contract operations, so the registry is tested
with glsim's `SimEngine`, bundled with genlayer-test. After
`engine.activate()` installs the cross-contract hook, an emitted call is
enqueued and drained for real and the receiver sees the calling contract as
sender. Nothing in `site-packages` is patched and no mock simulates delivery.

Known harness limit: deploying the same contract file twice in one glsim
process trips the SDK's class registry, so the spoofing test uses a single
instance across its registration boundary rather than two reporters.

### Not wired into the escrow yet

`ProjectEscrow` does **not** emit reports to a registry. Doing so would add an
emitted call to terminal paths in frozen Phase 3 code, and Direct Mode cannot
execute or observe it, so every existing Phase 1–3 test would exercise a
branch that silently does nothing there. The registry is therefore delivered
standalone and verified against a real emitted call from a reporter contract.
Wiring it into the escrow is a deliberate follow-up, and belongs with the
Bradbury deployment in Phase 6 where the delivery can actually be observed.

## Verification status

| Item | Verified where |
| --- | --- |
| Interface views | Direct Mode |
| GitHub gate, all branches | Direct Mode with mocked web responses |
| Registry authentication and tallies | glsim, real emitted calls |
| Emitted write delivery between contracts | **Bradbury**, probe L8 |
| Synchronous cross-contract view read | glsim only, **not Bradbury** |
| Live GitHub API shape and rate limit | Bradbury-era Phase 0 measurement |

No Phase 4 contract has been deployed to Bradbury, and no network transaction
was sent during this phase.
