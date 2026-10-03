"""Phase 3: two-party mutual settlement.

The settleable pot is exactly `locked`. Unmatched value, appeal credit,
appeal bonds and anything already queued, in flight or bounced are excluded,
because none of them is unallocated escrow principal. Native balance is never
the pot.
"""

import json

import pytest

CONTRACT = "contracts/project_escrow.py"

ALLOWED_SOURCES = "raw.githubusercontent.com/Shahin021/Escrow"

SPEC = "Deliver the signup page with an email field and a submit button."

URL_A = (
    "https://raw.githubusercontent.com/Shahin021/Escrow/"
    "4fefef6a1d790a2fb39a62a937e76852c6cb778c/evidence/nova_valid.html"
)

EVIDENCE = """
Nova signup

Email address
Submit

Privacy policy
"""

HOUR = 3600
DAY = 86400

T0 = "2026-09-21T09:33:00Z"
T0_EPOCH = 1789983180

M0 = 600
M1 = 400
TOTAL = M0 + M1
BOND = 60


def set_chain_time(iso):
    import genlayer

    genlayer.gl.message_raw["datetime"] = iso


def at(epoch_seconds):
    import datetime as dt

    return (
        dt.datetime.fromtimestamp(epoch_seconds, dt.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _worker(address):
    return "0x" + address.hex()


def _expect_revert(message, fn):
    with pytest.raises(Exception) as err:
        fn()

    assert message in str(err.value)


def _identity(escrow):
    acc = escrow.get_accounting()

    left = (
        int(acc["funded"])
        + int(acc["appeal_credit_received"])
        + int(acc["unmatched_returns"])
    )
    right = (
        int(acc["locked"])
        + int(acc["appeal_credit_held"])
        + int(acc["appeal_bond_held"])
        + int(acc["queued_out"])
        + int(acc["inflight_out"])
        + int(acc["bounced_held"])
        + int(acc["unmatched_held"])
        + int(acc["sent_total"])
    )

    assert left == right, f"identity broken: {left} != {right}"

    return True


def _resident(escrow):
    acc = escrow.get_accounting()

    return (
        int(acc["locked"])
        + int(acc["appeal_credit_held"])
        + int(acc["appeal_bond_held"])
        + int(acc["queued_out"])
        + int(acc["bounced_held"])
        + int(acc["unmatched_held"])
    )


def _complete_transfer(direct_vm, escrow):
    direct_vm.deal(direct_vm._contract_address, _resident(escrow))


def _mock(direct_vm, criteria=None, status=200, body=EVIDENCE):
    direct_vm.clear_mocks()
    direct_vm.mock_web(
        r".*raw\.githubusercontent\.com.*",
        {"method": "GET", "status": status, "body": body},
    )

    if criteria is not None:
        direct_vm.mock_llm(
            r".*",
            json.dumps({"criteria": criteria, "reason": "checked"}),
        )


def _funded(direct_vm, direct_deploy, direct_owner, direct_alice):
    direct_vm.sender = direct_owner

    escrow = direct_deploy(
        CONTRACT,
        _worker(direct_alice),
        json.dumps(
            [
                {"spec": SPEC, "amount": str(M0)},
                {"spec": SPEC, "amount": str(M1)},
            ]
        ),
        ALLOWED_SOURCES,
        "text",
        1,
        False,
    )

    set_chain_time(T0)
    direct_vm.deal(direct_owner, TOTAL * 8)
    direct_vm.sender = direct_owner
    direct_vm.value = TOTAL

    try:
        escrow.fund()
    finally:
        direct_vm.value = 0

    direct_vm.deal(direct_vm._contract_address, TOTAL)

    return escrow


def _propose(direct_vm, escrow, sender, to_worker, to_client):
    direct_vm.sender = sender
    escrow.propose_settlement(str(to_worker), str(to_client))


def _accept(direct_vm, escrow, sender):
    direct_vm.sender = sender
    escrow.accept_settlement(int(escrow.get_settlement()["nonce"]))


# ------------------------------------------------------------ happy paths


@pytest.mark.parametrize(
    "to_worker,to_client",
    [(500, 500), (700, 300), (TOTAL, 0), (0, TOTAL)],
)
def test_both_parties_can_settle_any_exact_split(
    direct_vm,
    direct_deploy,
    direct_owner,
    direct_alice,
    to_worker,
    to_client,
):
    escrow = _funded(direct_vm, direct_deploy, direct_owner, direct_alice)

    _propose(direct_vm, escrow, direct_owner, to_worker, to_client)

    proposal = escrow.get_settlement()

    assert proposal["active"] is True
    assert proposal["to_worker"] == str(to_worker)
    assert proposal["to_client"] == str(to_client)

    _accept(direct_vm, escrow, direct_alice)

    assert escrow.get_project_status() == "SETTLING"
    assert escrow.get_locked() == "0"
    assert escrow.get_milestone_status(0) == "CANCELLED"
    assert escrow.get_milestone_status(1) == "CANCELLED"
    assert escrow.get_settlement()["active"] is False
    assert _identity(escrow)

    # A zero side queues nothing: no zero-value transfer is ever emitted.
    expected_legs = (1 if to_worker else 0) + (1 if to_client else 0)

    assert int(escrow.get_outflow_count()) == expected_legs

    for leg in range(expected_legs):
        _complete_transfer(direct_vm, escrow)
        escrow.confirm_outflow(leg)
        assert _identity(escrow)

    assert escrow.get_total_settled_worker() == str(to_worker)
    assert escrow.get_total_settled_client() == str(to_client)
    # Settlement is its own outcome, not a payout or a refund.
    assert escrow.get_total_released() == "0"
    assert escrow.get_total_refunded() == "0"

    escrow.close_project()

    assert escrow.get_project_status() == "CLOSED"
    assert _identity(escrow)


def test_worker_may_propose_and_client_may_accept(
    direct_vm,
    direct_deploy,
    direct_owner,
    direct_alice,
):
    escrow = _funded(direct_vm, direct_deploy, direct_owner, direct_alice)

    _propose(direct_vm, escrow, direct_alice, 400, 600)
    _accept(direct_vm, escrow, direct_owner)

    assert escrow.get_project_status() == "SETTLING"
    assert _identity(escrow)


def test_legs_are_serialized_and_the_project_stays_settling(
    direct_vm,
    direct_deploy,
    direct_owner,
    direct_alice,
):
    escrow = _funded(direct_vm, direct_deploy, direct_owner, direct_alice)

    _propose(direct_vm, escrow, direct_owner, 600, 400)
    _accept(direct_vm, escrow, direct_alice)

    assert escrow.get_outflow_status(0) == "EMITTED"
    assert escrow.get_outflow_status(1) == "QUEUED"
    assert escrow.get_inflight_out() == "600"
    assert escrow.get_queued_out() == "400"

    _expect_revert(
        "project still has unsettled obligations",
        lambda: escrow.close_project(),
    )

    _complete_transfer(direct_vm, escrow)
    escrow.confirm_outflow(0)

    assert escrow.get_project_status() == "SETTLING"
    assert escrow.get_outflow_status(1) == "EMITTED"

    _expect_revert(
        "project still has unsettled obligations",
        lambda: escrow.close_project(),
    )

    _complete_transfer(direct_vm, escrow)
    escrow.confirm_outflow(1)

    escrow.close_project()

    assert escrow.get_project_status() == "CLOSED"
    assert _identity(escrow)


def test_bounced_settlement_leg_is_owned_by_its_recipient(
    direct_vm,
    direct_deploy,
    direct_owner,
    direct_alice,
    direct_bob,
):
    escrow = _funded(direct_vm, direct_deploy, direct_owner, direct_alice)

    _propose(direct_vm, escrow, direct_owner, 600, 400)
    _accept(direct_vm, escrow, direct_alice)

    direct_vm.value = 600

    try:
        escrow.__on_errored_message__()
    finally:
        direct_vm.value = 0

    assert escrow.get_outflow_status(0) == "BOUNCED"
    assert _identity(escrow)

    for sender in (direct_owner, direct_bob):
        direct_vm.sender = sender
        _expect_revert(
            "only the worker can redirect this outflow",
            lambda: escrow.redirect_outflow(0, "0x" + sender.hex()),
        )

    direct_vm.sender = direct_alice
    escrow.redirect_outflow(0, "0x" + direct_alice.hex())

    _complete_transfer(direct_vm, escrow)
    escrow.confirm_outflow(0)

    assert escrow.get_total_settled_worker() == "600"
    assert _identity(escrow)


# --------------------------------------------------------- failure paths


def test_split_must_match_the_locked_principal_exactly(
    direct_vm,
    direct_deploy,
    direct_owner,
    direct_alice,
):
    escrow = _funded(direct_vm, direct_deploy, direct_owner, direct_alice)

    for to_worker, to_client in ((500, 499), (500, 501), (0, 0)):
        direct_vm.sender = direct_owner

        _expect_revert(
            "settlement must allocate exactly the locked principal",
            lambda w=to_worker, c=to_client: escrow.propose_settlement(
                str(w), str(c)
            ),
        )

    assert escrow.get_settlement()["active"] is False
    assert escrow.get_locked() == str(TOTAL)
    assert _identity(escrow)


def test_only_the_parties_can_propose_or_accept(
    direct_vm,
    direct_deploy,
    direct_owner,
    direct_alice,
    direct_bob,
):
    escrow = _funded(direct_vm, direct_deploy, direct_owner, direct_alice)

    direct_vm.sender = direct_bob

    _expect_revert(
        "only the client or the worker can settle",
        lambda: escrow.propose_settlement("500", "500"),
    )

    _propose(direct_vm, escrow, direct_owner, 500, 500)

    direct_vm.sender = direct_bob

    _expect_revert(
        "only the client or the worker can settle",
        lambda: escrow.accept_settlement(
            int(escrow.get_settlement()["nonce"])
        ),
    )

    assert escrow.get_project_status() == "ACTIVE"
    assert _identity(escrow)


def test_proposer_cannot_accept_its_own_proposal(
    direct_vm,
    direct_deploy,
    direct_owner,
    direct_alice,
):
    escrow = _funded(direct_vm, direct_deploy, direct_owner, direct_alice)

    _propose(direct_vm, escrow, direct_owner, 500, 500)

    direct_vm.sender = direct_owner

    _expect_revert(
        "the proposer cannot accept its own proposal",
        lambda: escrow.accept_settlement(
            int(escrow.get_settlement()["nonce"])
        ),
    )

    assert escrow.get_locked() == str(TOTAL)
    assert _identity(escrow)


def test_a_replaced_proposal_cannot_be_accepted(
    direct_vm,
    direct_deploy,
    direct_owner,
    direct_alice,
):
    escrow = _funded(direct_vm, direct_deploy, direct_owner, direct_alice)

    _propose(direct_vm, escrow, direct_owner, 900, 100)
    stale_nonce = int(escrow.get_settlement()["nonce"])

    # The client replaces its offer with a worse one for the worker.
    _propose(direct_vm, escrow, direct_owner, 100, 900)

    direct_vm.sender = direct_alice

    _expect_revert(
        "settlement proposal is stale",
        lambda: escrow.accept_settlement(stale_nonce),
    )

    assert escrow.get_locked() == str(TOTAL)
    assert escrow.get_settlement()["to_worker"] == "100"
    assert _identity(escrow)


def test_a_withdrawn_proposal_cannot_be_accepted(
    direct_vm,
    direct_deploy,
    direct_owner,
    direct_alice,
):
    escrow = _funded(direct_vm, direct_deploy, direct_owner, direct_alice)

    _propose(direct_vm, escrow, direct_owner, 500, 500)
    nonce = int(escrow.get_settlement()["nonce"])

    direct_vm.sender = direct_alice
    _expect_revert(
        "only the proposer can withdraw the proposal",
        lambda: escrow.withdraw_settlement(),
    )

    direct_vm.sender = direct_owner
    escrow.withdraw_settlement()

    assert escrow.get_settlement()["active"] is False

    direct_vm.sender = direct_alice
    _expect_revert(
        "no active settlement proposal",
        lambda: escrow.accept_settlement(nonce),
    )

    assert escrow.get_locked() == str(TOTAL)
    assert _identity(escrow)


def test_settlement_cannot_be_accepted_twice(
    direct_vm,
    direct_deploy,
    direct_owner,
    direct_alice,
):
    escrow = _funded(direct_vm, direct_deploy, direct_owner, direct_alice)

    _propose(direct_vm, escrow, direct_owner, 500, 500)
    nonce = int(escrow.get_settlement()["nonce"])

    _accept(direct_vm, escrow, direct_alice)

    outflows = int(escrow.get_outflow_count())

    direct_vm.sender = direct_alice
    _expect_revert(
        "project is not active",
        lambda: escrow.accept_settlement(nonce),
    )

    assert int(escrow.get_outflow_count()) == outflows
    assert escrow.get_locked() == "0"
    assert _identity(escrow)


def test_settlement_is_blocked_while_an_outflow_is_pending(
    direct_vm,
    direct_deploy,
    direct_owner,
    direct_alice,
):
    escrow = _funded(direct_vm, direct_deploy, direct_owner, direct_alice)

    direct_vm.sender = direct_alice
    escrow.submit_deliverable(0, URL_A, "")

    _mock(direct_vm, criteria=[True])
    escrow.resolve(0)

    # An approved milestone is already owed payment.
    direct_vm.sender = direct_owner
    _expect_revert(
        "a milestone is already owed payment or refund",
        lambda: escrow.propose_settlement("400", "600"),
    )

    direct_vm.sender = direct_alice
    escrow.claim_payment(0)

    # And now value is actually in flight.
    direct_vm.sender = direct_owner
    _expect_revert(
        "settlement requires an idle outflow ledger",
        lambda: escrow.propose_settlement("200", "200"),
    )

    assert _identity(escrow)


def test_settlement_is_blocked_while_a_bond_is_unresolved(
    direct_vm,
    direct_deploy,
    direct_owner,
    direct_alice,
):
    escrow = _funded(direct_vm, direct_deploy, direct_owner, direct_alice)

    direct_vm.sender = direct_alice
    escrow.submit_deliverable(0, URL_A, "")

    _mock(direct_vm, criteria=[False])
    escrow.resolve(0)

    assert escrow.get_milestone_status(0) == "REJECTED_FINAL"

    direct_vm.sender = direct_alice
    direct_vm.value = BOND

    try:
        escrow.fund_appeal_credit()
    finally:
        direct_vm.value = 0

    direct_vm.deal(direct_vm._contract_address, TOTAL + BOND)

    direct_vm.sender = direct_alice
    escrow.appeal(0, "")

    # Bond ownership is undecided until the appeal resolves.
    direct_vm.sender = direct_owner
    _expect_revert(
        "settlement requires the appeal bond to be resolved",
        lambda: escrow.propose_settlement("500", "500"),
    )

    assert escrow.get_appeal_bond_held() == str(BOND)
    assert _identity(escrow)


def test_worker_credit_is_not_part_of_the_pot(
    direct_vm,
    direct_deploy,
    direct_owner,
    direct_alice,
):
    escrow = _funded(direct_vm, direct_deploy, direct_owner, direct_alice)

    direct_vm.sender = direct_alice
    direct_vm.value = 250

    try:
        escrow.fund_appeal_credit()
    finally:
        direct_vm.value = 0

    direct_vm.deal(direct_vm._contract_address, TOTAL + 250)

    # The pot is `locked` only: the worker's own credit is excluded.
    direct_vm.sender = direct_owner
    _expect_revert(
        "settlement must allocate exactly the locked principal",
        lambda: escrow.propose_settlement("500", "750"),
    )

    _propose(direct_vm, escrow, direct_owner, 500, 500)
    _accept(direct_vm, escrow, direct_alice)

    assert escrow.get_appeal_credit_held() == "250"
    assert _identity(escrow)

    # Credit survives settlement and is still withdrawable afterwards.
    direct_vm.sender = direct_alice
    escrow.withdraw_appeal_credit("250")

    assert escrow.get_appeal_credit_held() == "0"
    assert _identity(escrow)


def test_milestone_actions_are_frozen_after_settlement(
    direct_vm,
    direct_deploy,
    direct_owner,
    direct_alice,
):
    escrow = _funded(direct_vm, direct_deploy, direct_owner, direct_alice)

    _propose(direct_vm, escrow, direct_owner, 500, 500)
    _accept(direct_vm, escrow, direct_alice)

    direct_vm.sender = direct_alice

    with pytest.raises(Exception):
        escrow.submit_deliverable(0, URL_A, "")

    with pytest.raises(Exception):
        escrow.claim_payment(0)

    assert escrow.get_milestone_status(0) == "CANCELLED"
    assert _identity(escrow)


def test_settlement_needs_principal_to_split(
    direct_vm,
    direct_deploy,
    direct_owner,
    direct_alice,
):
    direct_vm.sender = direct_owner

    escrow = direct_deploy(
        CONTRACT,
        _worker(direct_alice),
        json.dumps([{"spec": SPEC, "amount": str(M0)}]),
        ALLOWED_SOURCES,
        "text",
        1,
        False,
    )

    set_chain_time(T0)

    # Before funding there is nothing to settle, and the project is not
    # active yet either.
    direct_vm.sender = direct_owner
    _expect_revert(
        "project is not active",
        lambda: escrow.propose_settlement("0", "0"),
    )


@pytest.mark.parametrize("amount", ["abc", "-1", "\uff15\uff10"])
def test_invalid_amount_strings_are_rejected(
    direct_vm,
    direct_deploy,
    direct_owner,
    direct_alice,
    amount,
):
    escrow = _funded(direct_vm, direct_deploy, direct_owner, direct_alice)

    direct_vm.sender = direct_owner

    _expect_revert(
        "amount must be a decimal string",
        lambda: escrow.propose_settlement(amount, "500"),
    )

    assert escrow.get_settlement()["active"] is False
    assert _identity(escrow)
