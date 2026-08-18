# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""
EscrowWithIntelligentReview
============================
A reusable Intelligent Contract primitive for GenLayer: a two-party escrow
where a natural-language deliverable is judged against a natural-language
specification by GenLayer's validator set, instead of by a trusted third
party or a rigid on-chain keyword rubric.

Deploy one instance per agreement (worker, spec, and agreed_amount are
constructor arguments) - the same contract code is reused for every deal.

See README.md for the full design rationale (in particular, why resolve()
uses a custom validator_fn with partial field matching rather than the
strict_eq or prompt_comparative convenience wrappers) and for instructions
on running the tests.
"""

from genlayer import *
import json


@gl.evm.contract_interface
class _Recipient:
    class View:
        pass

    class Write:
        pass


class EscrowWithIntelligentReview(gl.Contract):
    # ---- persistent state --------------------------------------------
    client: Address
    worker: Address
    spec: str
    deliverable: str
    verdict_reason: str
    status: str  # AWAITING_DEPOSIT | AWAITING_DELIVERY | UNDER_REVIEW
                 # | APPROVED | RELEASED | DISPUTED | REFUNDED
    agreed_amount: u256
    revision_count: u32
    max_revisions: u32

    def __init__(self, worker: str, spec: str, agreed_amount: str, max_revisions: int = 3):
        """
        Deployed by the client - the deploying account becomes `self.client`.

        worker         - address of the party that will deliver the work
        spec           - free-text specification of what counts as "done"
        agreed_amount  - amount the client must deposit via fund(), given as
                         a base-10 string to avoid JSON-number precision
                         issues with large token amounts
        max_revisions  - how many reject -> resubmit cycles are allowed
                         before the contract locks into a disputed state
                         that lets the client reclaim the funds
        """
        self.client = gl.message.sender_address
        self.worker = Address(worker)
        self.spec = spec
        self.deliverable = ""
        self.verdict_reason = ""
        self.status = "AWAITING_DEPOSIT"
        self.agreed_amount = u256(int(agreed_amount))
        self.revision_count = u32(0)
        self.max_revisions = u32(max_revisions)

    # ---- step 1: client funds the escrow -------------------------------
    @gl.public.write.payable
    def fund(self) -> None:
        if gl.message.sender_address != self.client:
            raise gl.vm.UserError("only the client can fund this escrow")
        if self.status != "AWAITING_DEPOSIT":
            raise gl.vm.UserError(f"cannot fund from status {self.status}")
        if gl.message.value != self.agreed_amount:
            raise gl.vm.UserError("sent value does not match the agreed amount")
        self.status = "AWAITING_DELIVERY"

    # ---- step 2: worker submits (or resubmits) the deliverable ----------
    @gl.public.write
    def submit_deliverable(self, deliverable: str) -> None:
        if gl.message.sender_address != self.worker:
            raise gl.vm.UserError("only the worker can submit")
        if self.status != "AWAITING_DELIVERY":
            raise gl.vm.UserError(f"cannot submit from status {self.status}")
        if len(deliverable) == 0:
            raise gl.vm.UserError("deliverable cannot be empty")
        self.deliverable = deliverable
        self.status = "UNDER_REVIEW"

    # ---- step 3: permissionless resolution via the Equivalence Principle
    @gl.public.write
    def resolve(self) -> None:
        """
        Anyone can call this once a deliverable is under review - it does
        not have to be the client or the worker, which keeps resolution
        trustless (neither side can stall the outcome by refusing to call
        it). The actual judgment call is made by the validator set.
        """
        if self.status != "UNDER_REVIEW":
            raise gl.vm.UserError(f"cannot resolve from status {self.status}")

        # Plain str fields are already ordinary Python values once read out
        # of storage, so a normal local assignment is enough to make them
        # available inside the closure below (copy_to_memory is only for
        # storage-view container/dataclass types, not primitives like str).
        spec_copy = self.spec
        deliverable_copy = self.deliverable

        def leader_fn() -> dict:
            prompt = f"""
You are an impartial reviewer adjudicating a freelance escrow agreement.

AGREED SPECIFICATION:
---
{spec_copy}
---

SUBMITTED DELIVERABLE:
---
{deliverable_copy}
---

In good faith, decide whether the deliverable satisfies the specification.
Respond with ONLY a JSON object, no other text, in exactly this shape:
{{"approved": true or false, "reason": "<one concise sentence>"}}
"""
            raw = gl.nondet.exec_prompt(prompt)
            if isinstance(raw, dict):
                # Some runtimes/test harnesses return already-parsed JSON.
                data = raw
            else:
                cleaned = raw.strip().strip("`")
                if cleaned.lower().startswith("json"):
                    cleaned = cleaned[4:].strip()
                data = json.loads(cleaned)
            return {"approved": bool(data["approved"]), "reason": str(data["reason"])[:300]}

        def validator_fn(leader_result) -> bool:
            if not isinstance(leader_result, gl.vm.Return):
                return False
            validator_data = leader_fn()
            # Partial field matching: only the `approved` decision has to
            # agree exactly. Free-text `reason` is expected to vary in
            # wording between independently-run validators even when they
            # reach the same verdict, so comparing it (as strict_eq or the
            # prompt_comparative convenience wrapper effectively would)
            # would make consensus fail for reasons unrelated to the
            # actual decision. Every validator independently re-runs the
            # judgment here - stronger than only grading the leader's
            # stated quality, since a single compromised leader can't
            # dictate the outcome.
            return leader_result.calldata["approved"] == validator_data["approved"]

        verdict = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

        self.verdict_reason = verdict["reason"]

        if verdict["approved"]:
            self.status = "APPROVED"
        else:
            self.revision_count = u32(self.revision_count + 1)
            if self.revision_count >= self.max_revisions:
                self.status = "DISPUTED"
            else:
                self.status = "AWAITING_DELIVERY"

    # ---- step 4: worker claims an approved payment -----------------------
    @gl.public.write
    def claim_payment(self) -> None:
        if gl.message.sender_address != self.worker:
            raise gl.vm.UserError("only the worker can claim payment")
        if self.status != "APPROVED":
            raise gl.vm.UserError(f"cannot claim payment from status {self.status}")
        if self.balance < self.agreed_amount:
            raise gl.vm.UserError("escrow balance is insufficient for payment")

        _Recipient(self.worker).emit_transfer(value=self.agreed_amount)

    @gl.public.write
    def confirm_payment(self) -> None:
        if self.status != "APPROVED":
            raise gl.vm.UserError(f"cannot confirm payment from status {self.status}")
        if self.balance != u256(0):
            raise gl.vm.UserError("payment has not completed yet")

        self.status = "RELEASED"

    # ---- step 5 (only reached after max_revisions is exceeded) ----------
    @gl.public.write
    def claim_refund(self) -> None:
        if gl.message.sender_address != self.client:
            raise gl.vm.UserError("only the client can claim a refund")
        if self.status != "DISPUTED":
            raise gl.vm.UserError(f"cannot refund from status {self.status}")
        self.status = "REFUNDED"
        _Recipient(self.client).emit_transfer(value=self.agreed_amount)

    # ---- read-only views --------------------------------------------------
    @gl.public.view
    def get_status(self) -> str:
        return self.status

    @gl.public.view
    def get_spec(self) -> str:
        return self.spec

    @gl.public.view
    def get_deliverable(self) -> str:
        return self.deliverable

    @gl.public.view
    def get_verdict_reason(self) -> str:
        return self.verdict_reason

    @gl.public.view
    def get_revision_count(self) -> u32:
        return self.revision_count

    @gl.public.view
    def get_agreed_amount(self) -> u256:
        return self.agreed_amount
