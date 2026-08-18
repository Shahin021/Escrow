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
| `APPROVED`          | Validators approved the work; worker can claim payment    |
| `RELEASED`          | Payment completed and confirmed                           |
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

- `escrow_contract.py` - the contract
- `test_escrow_contract.py` - Direct Mode tests (`genlayer-test`):
  the happy path, the revision/dispute loop, two access-control checks,
  and a test that exercises validator disagreement directly
- `README.md` - project documentation

## Running it

```bash
pip install genlayer-test
pytest test_escrow_contract.py -v
```

To try it in GenLayer Studio instead: paste `escrow_contract.py`
in, deploy with constructor args `(worker_address, spec_text, "1000", 3)`,
then call `fund`, `submit_deliverable`, `resolve`, `claim_payment`,
and finally `confirm_payment`, watching `get_status()` move through the
state machine above.

## Live Asimov verification

The complete approval and payout path was executed on GenLayer Asimov.

**Contract**

`0x888d5cc8018A31d6B91B69C7936b4F7bc1e063a8`

**Fund**

`0xd8899009085ddd5d0e6a79a639889652e0c1366e72d4e958dbef445630d5e319`

Result: `AWAITING_DEPOSIT -> AWAITING_DELIVERY`, contract balance `1 GEN`.

**Submit deliverable**

`0x3ce562ac06d0af257dec6f80961775c5ce8e048e5fcfdb5736c0cf9d1b99dd6e`

Result: `AWAITING_DELIVERY -> UNDER_REVIEW`.

**Intelligent resolve**

`0xcebe901a96f995c247bdefc0aa2c68ecaac2848ce1a146dc1dc1cd0b8dfb8dc9`

Result: `UNDER_REVIEW -> APPROVED`.

Validator reason:

> All required elements (heading, product description, working signup form) are present and functioning as specified.

The escrow still held the full `1 GEN` after adjudication.

**Worker payment**

`0xe30afa67886085118fa0dfbbcce3faa6a51c000b82a31862d6cee9395ddd5a62`

Verified result:

- Escrow balance: `1 GEN -> 0 GEN`
- Worker balance: `0.49560409929439625 GEN -> 1.4939768876137838 GEN`

The difference from exactly `+1 GEN` is transaction gas paid by the worker.

The approval decision and payment execution are intentionally separated:
`resolve()` establishes the validator verdict, while `claim_payment()` performs
the external value transfer. `confirm_payment()` can then move the escrow to
the terminal `RELEASED` state.

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
