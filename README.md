# EscrowWithIntelligentReview

A reusable GenLayer Intelligent Contract primitive: **two-party escrow where
the release decision is a judgment call on natural-language text, made by
GenLayer's validator set instead of a trusted arbiter or a rigid on-chain
checklist.**

Submitted for the **Intelligent Contracts** builder task.

## Why this is a primitive, not a demo

Any marketplace where one party pays for work described in prose - a
freelance gig, a bounty program (including the one this task is posted on),
a grant milestone, a translation job - needs someone to decide whether the
deliverable actually matches the brief. Today that's either a centralized
platform admin or a slow, expensive human dispute process. This contract
lets that decision be made on-chain, by consensus, directly from the two
texts involved (the spec and the deliverable), with no extra oracle,
off-chain judge, or fixed keyword rubric.

Deploy one instance per agreement - `worker`, `spec`, and `agreed_amount`
are constructor arguments, so the same contract code is reused for every
deal. That reusability is the point: it's a primitive other builders can
deploy as-is, not a one-off illustration of an API call.

## State machine

```
AWAITING_DEPOSIT --fund()--> AWAITING_DELIVERY --submit_deliverable()--> UNDER_REVIEW
                                     ^                                        |
                                     |            resolve(): rejected,       |
                                     +---- revisions remain -----------------+
                                                     |
                                                     | resolve(): approved
                                                     v
                                                 RELEASED

UNDER_REVIEW --resolve(): rejected, revision_count >= max_revisions--> DISPUTED --claim_refund()--> REFUNDED
```

| Status              | Meaning                                                |
| ------------------- | ------------------------------------------------------- |
| `AWAITING_DEPOSIT`  | Deployed, waiting for the client to fund it              |
| `AWAITING_DELIVERY` | Funded, waiting for the worker to submit                 |
| `UNDER_REVIEW`      | A deliverable is in, waiting for `resolve()`             |
| `RELEASED`          | Approved - funds sent to the worker (terminal)           |
| `DISPUTED`          | Rejected `max_revisions` times (terminal until refund)   |
| `REFUNDED`          | Client reclaimed the deposit (terminal)                  |

## How consensus is used

The judgment step lives entirely inside `resolve()`. It builds a prompt
containing the spec and the deliverable and asks the validator set to
decide, in the shape `{"approved": bool, "reason": str}`.

**Equivalence Principle choice: a custom `validator_fn` via
`gl.vm.run_nondet_unsafe`, doing partial field matching - not `strict_eq`,
not `prompt_non_comparative`, and not the `prompt_comparative` convenience
wrapper either.**

- `strict_eq` would require every validator's *entire* JSON output,
  including the free-text `reason`, to match byte-for-byte. Two validators
  can agree completely on the verdict and still phrase the reason
  differently, so `strict_eq` would make consensus fail for reasons that
  have nothing to do with the actual decision.
- `prompt_non_comparative` only asks the other validators to grade the
  *leader's* stated quality. A compromised or simply unlucky leader's
  output is what gets reviewed - the other validators never independently
  form their own opinion of the deliverable.
- `prompt_comparative` has every validator run the same judgment
  independently and routes the comparison through GenLayer's built-in
  `EqComparative` LLM template. That's a real improvement over
  non-comparative, but GenLayer's own current guidance is that most
  contracts outgrow the convenience wrappers: for a simple boolean
  decision field, a hand-written `validator_fn` that just compares
  `leader_data["approved"] == validator_data["approved"]` is deterministic,
  cheaper (no extra template call), and easier to reason about than
  routing a plain equality check through an LLM-based equivalence
  judgment.
- The custom `validator_fn` has every validator independently re-run the
  full judgment (via `leader_fn()` again) and compares only the `approved`
  field - `reason` is allowed to vary in wording. This is the pattern
  GenLayer's Equivalence Principle documentation calls "Partial Field
  Matching," and it's the strongest of the options above for a contract
  making a real, adversarially-relevant decision about who gets paid.

`resolve()` is deliberately **permissionless** - neither the client nor the
worker has to trigger it, so neither side can stall the outcome by simply
refusing to call it.

## Files

- `contracts/escrow_contract.py` - the contract
- `tests/test_escrow_contract.py` - Direct Mode tests (`genlayer-test`):
  the happy path, the revision/dispute loop, two access-control checks,
  and a test that exercises validator disagreement directly
- this file

## Running it

```bash
pip install genlayer-test
pytest tests/ -v
```

To try it in GenLayer Studio instead: paste `contracts/escrow_contract.py`
in, deploy with constructor args `(worker_address, spec_text, "1000", 3)`,
then call `fund`, `submit_deliverable`, and `resolve` from the Write
Methods panel, watching `get_status()` move through the state machine
above.

## Verification

This isn't just written-but-untested code: the full suite was actually run
against the real GenVM SDK (via `genlayer-test`'s Direct Mode, pinned to
the SDK build with dependency hash
`1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6`, with LLM calls
mocked). All 6 tests pass. That run caught and fixed several real issues
that reasoning from documentation alone had missed:

- `gl.storage.copy_to_memory()` is for storage-view container/dataclass
  types, not plain `str` fields - calling it on `self.spec` raised
  `AssertionError`. Fixed by reading the fields directly.
- `gl.ContractAt(...)` doesn't exist in the current SDK; the real function
  is `gl.get_contract_at(...)`.
- Plain `assert` statements work for reverting a transaction, but
  `gl.vm.UserError` is the documented idiomatic way to raise a user-facing
  contract error - and some tooling (including this test suite's
  `expect_revert` cheatcode) specifically expects it rather than a bare
  `AssertionError`.
- `gl.nondet.exec_prompt()` can come back pre-parsed as a `dict` depending
  on the runtime, not only as a JSON string - `resolve()` now handles
  both.

## Notes / possible extensions

- `agreed_amount` is passed as a string and parsed with `int()` to avoid
  any JSON-number precision surprises with large token amounts.
- A natural extension is a small `arbiter` address that can force-resolve
  a `DISPUTED` case in the worker's favor, for situations where it's the
  client acting in bad faith rather than the worker - right now a
  determined client always gets their money back after enough rejections.
- The prompt and `principle` text are intentionally short and explicit;
  worth reviewing GenLayer's own prompt-injection security guidance before
  adapting this contract for a higher-stakes deployment.
