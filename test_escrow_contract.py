import pytest

"""
Tests for EscrowWithIntelligentReview using the GenLayer Testing Suite
(genlayer-test) in Direct Mode: fast, in-process, no Docker/Studio needed.

Install:
    pip install genlayer-test

Run:
    pytest test_escrow_contract.py -v
"""

CONTRACT_PATH = "escrow_contract.py"
SPEC = "Deliver a landing page with a working email signup form."
GOOD_DELIVERABLE = (
    "Landing page deployed at example.com with a working signup form "
    "wired to Mailchimp."
)
BAD_DELIVERABLE = "Here is a link to my portfolio, unrelated to this project."
AMOUNT = 1000


def _deploy(direct_vm, direct_deploy, direct_owner, direct_bob, max_revisions=3):
    """Deploy as the client (direct_owner), naming direct_bob as the worker."""
    direct_vm.sender = direct_owner
    return direct_deploy(
        CONTRACT_PATH,
        "0x" + direct_bob.hex(),
        SPEC,
        str(AMOUNT),
        max_revisions,
        sdk_version="v0.2.12",
    )


def _fund(direct_vm, contract, sender, amount):
    direct_vm.sender = sender
    direct_vm.value = amount
    try:
        contract.fund()
    finally:
        direct_vm.value = 0


def test_happy_path_releases_funds_on_approval(direct_vm, direct_deploy, direct_owner, direct_bob):
    contract = _deploy(direct_vm, direct_deploy, direct_owner, direct_bob)
    assert contract.get_status() == "AWAITING_DEPOSIT"

    _fund(direct_vm, contract, direct_owner, AMOUNT)
    assert contract.get_status() == "AWAITING_DELIVERY"

    direct_vm.sender = direct_bob
    contract.submit_deliverable(GOOD_DELIVERABLE)
    assert contract.get_status() == "UNDER_REVIEW"

    # Every validator's LLM call is mocked to approve.
    direct_vm.mock_llm(r".*", '{"approved": true, "reason": "Meets the spec."}')

    contract.resolve()  # resolve() is permissionless - any sender works

    assert contract.get_status() == "RELEASED"
    assert contract.get_verdict_reason() != ""


def test_rejection_allows_resubmission_until_max_revisions(
    direct_vm, direct_deploy, direct_owner, direct_bob
):
    contract = _deploy(direct_vm, direct_deploy, direct_owner, direct_bob, max_revisions=3)

    _fund(direct_vm, contract, direct_owner, AMOUNT)

    direct_vm.mock_llm(r".*", '{"approved": false, "reason": "Missing the signup form."}')

    for i in range(3):
        direct_vm.sender = direct_bob
        contract.submit_deliverable(BAD_DELIVERABLE)
        contract.resolve()
        assert contract.get_revision_count() == i + 1
        if i < 2:
            assert contract.get_status() == "AWAITING_DELIVERY"

    assert contract.get_status() == "DISPUTED"

    direct_vm.sender = direct_owner
    contract.claim_refund()
    assert contract.get_status() == "REFUNDED"


def test_only_worker_can_submit(direct_vm, direct_deploy, direct_owner, direct_bob, direct_charlie):
    contract = _deploy(direct_vm, direct_deploy, direct_owner, direct_bob)
    _fund(direct_vm, contract, direct_owner, AMOUNT)

    direct_vm.sender = direct_charlie  # not the worker
    with pytest.raises(Exception, match="only the worker can submit"):
        contract.submit_deliverable("sneaky submission")


def test_only_client_can_fund(direct_vm, direct_deploy, direct_owner, direct_bob, direct_charlie):
    contract = _deploy(direct_vm, direct_deploy, direct_owner, direct_bob)

    with pytest.raises(Exception, match="only the client can fund this escrow"):
        _fund(direct_vm, contract, direct_charlie, AMOUNT)


def test_wrong_deposit_amount_reverts(direct_vm, direct_deploy, direct_owner, direct_bob):
    contract = _deploy(direct_vm, direct_deploy, direct_owner, direct_bob)

    with pytest.raises(Exception, match="sent value does not match the agreed amount"):
        _fund(direct_vm, contract, direct_owner, AMOUNT - 1)


def test_validator_disagreement_is_detected(direct_vm, direct_deploy, direct_owner, direct_bob):
    """
    Exercises the custom validator_fn directly - the piece that
    distinguishes a real consensus contract from a single-LLM-call demo.
    The leader approves; a validator whose independent re-run of the same
    judgment is mocked to disagree is correctly flagged as dissenting.
    """
    contract = _deploy(direct_vm, direct_deploy, direct_owner, direct_bob)
    _fund(direct_vm, contract, direct_owner, AMOUNT)
    direct_vm.sender = direct_bob
    contract.submit_deliverable(GOOD_DELIVERABLE)

    direct_vm.mock_llm(r".*", '{"approved": true, "reason": "Satisfies requirements."}')
    contract.resolve()  # runs as leader, captures the leader's verdict

    direct_vm.clear_mocks()
    direct_vm.mock_llm(r".*", '{"approved": false, "reason": "Does not satisfy requirements."}')
    assert direct_vm.run_validator() is False  # a differing verdict is correctly caught
