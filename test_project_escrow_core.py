import json

CONTRACT = "contracts/project_escrow.py"

M1 = 1000
M2 = 2500
M3 = 700
TOTAL = M1 + M2 + M3

MILESTONES = json.dumps(
    [
        {
            "spec": "Deliver the project skeleton and documented architecture.",
            "amount": str(M1),
        },
        {
            "spec": "Deliver the functional implementation and integration tests.",
            "amount": str(M2),
        },
        {
            "spec": "Deliver the final reviewed release and documentation.",
            "amount": str(M3),
        },
    ]
)

ALLOWED_SOURCES = "raw.githubusercontent.com/Shahin021/Escrow"


def _worker(address):
    return "0x" + address.hex()


def _deploy(direct_vm, direct_deploy, direct_owner, direct_alice):
    direct_vm.sender = direct_owner

    return direct_deploy(
        CONTRACT,
        _worker(direct_alice),
        MILESTONES,
        ALLOWED_SOURCES,
        "text",
        3,
        False,
    )


def _fund(direct_vm, contract, sender, value):
    direct_vm.sender = sender
    direct_vm.value = value
    try:
        contract.fund()
    finally:
        direct_vm.value = 0


def test_initial_multi_milestone_state(
    direct_vm,
    direct_deploy,
    direct_owner,
    direct_alice,
):
    escrow = _deploy(
        direct_vm,
        direct_deploy,
        direct_owner,
        direct_alice,
    )

    assert escrow.get_project_status() == "AWAITING_DEPOSIT"
    assert int(escrow.get_milestone_count()) == 3
    assert int(escrow.get_active_milestone()) == 0

    assert escrow.get_total_required() == str(TOTAL)
    assert escrow.get_total_funded() == "0"
    assert escrow.get_total_released() == "0"
    assert escrow.get_total_refunded() == "0"

    assert escrow.get_milestone_amount(0) == str(M1)
    assert escrow.get_milestone_amount(1) == str(M2)
    assert escrow.get_milestone_amount(2) == str(M3)

    assert escrow.get_milestone_status(0) == "LOCKED"
    assert escrow.get_milestone_status(1) == "LOCKED"
    assert escrow.get_milestone_status(2) == "LOCKED"

    assert int(escrow.get_milestone_revision_count(0)) == 0
    assert int(escrow.get_milestone_revision_count(1)) == 0
    assert int(escrow.get_milestone_revision_count(2)) == 0


def test_exact_project_funding_activates_only_first_milestone(
    direct_vm,
    direct_deploy,
    direct_owner,
    direct_alice,
):
    escrow = _deploy(
        direct_vm,
        direct_deploy,
        direct_owner,
        direct_alice,
    )

    direct_vm.deal(direct_owner, TOTAL * 2)

    _fund(
        direct_vm,
        escrow,
        direct_owner,
        TOTAL,
    )

    assert escrow.get_project_status() == "ACTIVE"
    assert escrow.get_total_funded() == str(TOTAL)

    assert escrow.get_milestone_status(0) == "AWAITING_DELIVERY"
    assert escrow.get_milestone_status(1) == "LOCKED"
    assert escrow.get_milestone_status(2) == "LOCKED"


def test_wrong_total_funding_reverts_without_credit(
    direct_vm,
    direct_deploy,
    direct_owner,
    direct_alice,
):
    escrow = _deploy(
        direct_vm,
        direct_deploy,
        direct_owner,
        direct_alice,
    )

    direct_vm.deal(direct_owner, TOTAL * 2)

    direct_vm.sender = direct_owner
    direct_vm.value = TOTAL - 1

    try:
        with direct_vm.expect_revert(
            "does not match the total required amount"
        ):
            escrow.fund()
    finally:
        direct_vm.value = 0

    assert escrow.get_project_status() == "AWAITING_DEPOSIT"
    assert escrow.get_total_funded() == "0"


def test_non_client_cannot_fund(
    direct_vm,
    direct_deploy,
    direct_owner,
    direct_alice,
    direct_bob,
):
    escrow = _deploy(
        direct_vm,
        direct_deploy,
        direct_owner,
        direct_alice,
    )

    direct_vm.deal(direct_bob, TOTAL * 2)

    direct_vm.sender = direct_bob
    direct_vm.value = TOTAL

    try:
        with direct_vm.expect_revert("only the client can fund"):
            escrow.fund()
    finally:
        direct_vm.value = 0

    assert escrow.get_total_funded() == "0"


def test_double_funding_is_rejected(
    direct_vm,
    direct_deploy,
    direct_owner,
    direct_alice,
):
    escrow = _deploy(
        direct_vm,
        direct_deploy,
        direct_owner,
        direct_alice,
    )

    direct_vm.deal(direct_owner, TOTAL * 3)

    _fund(direct_vm, escrow, direct_owner, TOTAL)

    direct_vm.sender = direct_owner
    direct_vm.value = TOTAL

    try:
        with direct_vm.expect_revert("cannot fund from status ACTIVE"):
            escrow.fund()
    finally:
        direct_vm.value = 0

    assert escrow.get_total_funded() == str(TOTAL)


def test_zero_amount_milestone_is_rejected(
    direct_vm,
    direct_deploy,
    direct_owner,
    direct_alice,
):
    bad = json.dumps(
        [
            {
                "spec": "Invalid zero-value milestone.",
                "amount": "0",
            }
        ]
    )

    direct_vm.sender = direct_owner

    with direct_vm.expect_revert("milestone amount must be positive"):
        direct_deploy(
            CONTRACT,
            _worker(direct_alice),
            bad,
            ALLOWED_SOURCES,
            "text",
            3,
            False,
        )


def test_screenshot_mode_is_not_supported_in_v3(
    direct_vm,
    direct_deploy,
    direct_owner,
    direct_alice,
):
    direct_vm.sender = direct_owner

    with direct_vm.expect_revert("render_mode must be text or html"):
        direct_deploy(
            CONTRACT,
            _worker(direct_alice),
            MILESTONES,
            ALLOWED_SOURCES,
            "screenshot",
            3,
            False,
        )
