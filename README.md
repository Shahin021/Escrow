# EscrowWithIntelligentReview

A reusable GenLayer Intelligent Contract primitive: **two-party escrow where**
**the release decision is a judgment call on natural-language text, made by**
**GenLayer's validator set instead of a trusted arbiter or a rigid on-chain**
**checklist.**

Submitted for the **Intelligent Contracts** builder task.

## Why this is a primitive, not a demo

Any marketplace where one party pays for work described in prose - a
freelance gig, a bounty program (including the one this task is posted on),
a grant milestone, a translation job - needs someone to decide whether the
deliverable actually matches the brief. Today that's either a centralized
platform admin or a slow, expensive human dispute process.

This contract lets that decision be made on-chain, by consensus, directly
from the two texts involved (the spec and the deliverable), with no extra
oracle, off-chain judge, or fixed keyword rubric.

Deploy one instance per agreement - `worker`, `spec`, and `agreed_amount`
are constructor arguments, so the same contract code is reused for every
deal. That reusability is the point: it's a primitive other builders can
deploy as-is, not a one-off illustration of an API call.

## State machine

```text
AWAITING_DEPOSIT --fund()--> AWAITING_DELIVERY --submit_deliverable()--> UNDER_REVIEW
                                     ^                                        |
                                     |            resolve(): rejected,       |
                                     +---- revisions remain -----------------+
                                                     |
                                                     | resolve(): approved
                                                     v
                                                 RELEASED

UNDER_REVIEW --resolve(): rejected, revision_count >= max_revisions--> DISPUTED --claim_refund()--> REFUNDED
