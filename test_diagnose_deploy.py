"""Offline coverage for the read-only deployment diagnostic.

The diagnostic must be safe to hand someone with a funded account: it may
only read. These tests drive it with a fake provider, so they assert both the
decoding logic and, more importantly, that nothing it does could sign or
broadcast.
"""

import importlib.util
import json
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

READ_ONLY_METHODS = {
    "eth_chainId",
    "eth_getCode",
    "eth_estimateGas",
    "eth_getBlockByNumber",
    "eth_getTransactionCount",
}

SENDER = "0x" + "11" * 20


def _load():
    spec = importlib.util.spec_from_file_location(
        "diagnose_deploy", os.path.join(REPO_ROOT, "scripts", "diagnose_deploy.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


class RaisingProvider:
    """Models genlayer-py 0.16.3's real provider.

    provider.py::_raise_on_error raises GenLayerError on ANY JSON-RPC error
    and keeps only code and message, discarding error.data. A diagnostic that
    routed through it could never print the revert payload, which is the
    whole point, so this fake raises the same way and the tests assert the
    diagnostic does not depend on it.
    """

    def __init__(self, estimate_response):
        self.calls = []
        self.estimate_response = estimate_response

    def make_request(self, method, params=None):
        self.calls.append(method)

        response = (
            self.estimate_response
            if method == "eth_estimateGas"
            else {"result": "0x1"}
        )

        if response.get("error"):
            from genlayer_py.exceptions import GenLayerError

            error = response["error"]

            raise GenLayerError(
                f"{method} failed (code={error.get('code')}): "
                f"{error.get('message')}"
            )

        return response


FakeProvider = RaisingProvider


def _client(estimate_response, captured=None):
    from genlayer_py import create_client
    from genlayer_py.chains import testnet_bradbury

    client = create_client(chain=testnet_bradbury)
    client.provider = RaisingProvider(estimate_response)

    return client


REVERT_WITH_DATA = {
    "jsonrpc": "2.0",
    "id": 1,
    "error": {
        "code": 3,
        "message": "execution reverted",
        "data": "0x90cb8b61",
    },
}


def _patch_rpc(monkeypatch, module, response, recorder):
    """Replace the raw JSON-RPC call, so nothing touches the network."""

    def fake_raw_rpc(url, method, params, timeout=60):
        recorder.append(method)

        return response if method == "eth_estimateGas" else {"result": "0x1"}

    monkeypatch.setattr(module, "raw_rpc", fake_raw_rpc)


def test_the_diagnostic_only_makes_read_only_calls():
    """The property that makes this safe to run with a funded account."""
    diag = _load()
    client = _client({"result": "0x5208"})

    diag.estimate(client, SENDER, diag.CONTROL)
    diag.estimate(client, SENDER, diag.ESCROW)

    assert set(client.provider.calls) <= READ_ONLY_METHODS

    for forbidden in ("eth_sendRawTransaction", "eth_sign", "personal_sign"):
        assert forbidden not in client.provider.calls


def test_it_needs_no_private_key():
    """Only an address is used; the encoder gets a stand-in with .address."""
    diag = _load()
    client = _client({"result": "0x5208"})

    result = diag.estimate(client, SENDER, diag.CONTROL)

    assert result["calldata_bytes"] > 0
    assert client.local_account is None


def test_the_control_payload_is_far_smaller_than_the_escrow():
    """The size discrimination the investigation depends on."""
    diag = _load()
    client = _client({"result": "0x5208"})

    control = diag.estimate(client, SENDER, diag.CONTROL)
    escrow = diag.estimate(client, SENDER, diag.ESCROW)

    assert escrow["calldata_bytes"] > control["calldata_bytes"] * 10


def test_a_value_bearing_estimate_is_still_only_an_estimate():
    diag = _load()
    client = _client({"result": "0x5208"})

    result = diag.estimate(client, SENDER, diag.ESCROW, value=1000)

    assert result["value"] == 1000
    assert set(client.provider.calls) <= READ_ONLY_METHODS


@pytest.mark.parametrize(
    "data,expected",
    [
        (None, "no revert data"),
        ("0x", "no revert data"),
        ("0x90cb8b61", "not found in the consensus ABIs"),
        ("0x4e487b7100000000000000000000000000000000000000000000000000000000000000" + "11", "Panic(uint256)"),
    ],
)
def test_revert_decoding_says_what_it_knows(data, expected):
    diag = _load()

    assert expected in diag.decode_revert(data)


def test_a_known_selector_is_named():
    """A selector that IS in the ABI must be reported with its signature."""
    diag = _load()
    table = diag.selector_table()

    assert table, "the consensus ABIs should yield selectors"

    selector = next(iter(table))

    assert selector in diag.decode_revert(selector)


def test_error_string_reverts_are_decoded():
    from eth_abi import encode

    diag = _load()
    payload = "0x08c379a0" + encode(["string"], ["insufficient fee"]).hex()

    assert "insufficient fee" in diag.decode_revert(payload)


def test_report_marks_failure_and_success(capsys):
    diag = _load()

    ok = diag.report("ok", {"source_bytes": 1, "calldata_bytes": 2, "value": 0,
                            "raw": {"result": "0x5208"}})
    assert ok is True

    failed = diag.report(
        "failed",
        {
            "source_bytes": 1,
            "calldata_bytes": 2,
            "value": 0,
            "raw": {"error": {"code": 3, "message": "execution reverted",
                              "data": "0x90cb8b61"}},
        },
    )

    assert failed is False

    printed = capsys.readouterr().out

    assert "raw JSON-RPC response" in printed
    assert "execution reverted" in printed
    assert "not found in the consensus ABIs" in printed


def test_a_revert_is_captured_even_though_the_sdk_provider_would_raise(
    monkeypatch, capsys
):
    """The flaw this fixes.

    The SDK provider raises GenLayerError and keeps only code and message, so
    a diagnostic built on it could never show error.data. The diagnostic now
    makes the JSON-RPC call directly, so the revert payload survives.
    """
    diag = _load()
    recorder = []

    _patch_rpc(monkeypatch, diag, REVERT_WITH_DATA, recorder)

    client = _client({"result": "0x5208"})
    result = diag.estimate(client, SENDER, diag.ESCROW)

    # The raising provider was never used for the estimate.
    assert client.provider.calls == []
    assert recorder == ["eth_estimateGas"]

    assert diag.report("subject", result) is False

    printed = capsys.readouterr().out

    assert "execution reverted" in printed
    assert "0x90cb8b61" in printed, "error.data must survive to the report"
    assert "not found in the consensus ABIs" in printed


def test_a_revert_without_data_says_so_rather_than_guessing(
    monkeypatch, capsys
):
    diag = _load()
    recorder = []

    _patch_rpc(
        monkeypatch,
        diag,
        {"error": {"code": 3, "message": "execution reverted"}},
        recorder,
    )

    client = _client({"result": "0x5208"})

    assert diag.report("subject", diag.estimate(client, SENDER, diag.ESCROW)) is False

    printed = capsys.readouterr().out

    assert "ABSENT" in printed
    assert "no decode is possible" in printed


def test_the_raw_rpc_helper_never_raises_on_a_jsonrpc_error(monkeypatch):
    """An error IS the result being sought, so it must be returned, not raised."""
    diag = _load()

    class FakeResponse:
        status_code = 200

        def json(self):
            return REVERT_WITH_DATA

    monkeypatch.setattr(
        "requests.post", lambda *args, **kwargs: FakeResponse()
    )

    out = diag.raw_rpc("http://example.invalid", "eth_estimateGas", [])

    assert out["error"]["data"] == "0x90cb8b61"


def test_an_unknown_selector_is_reported_as_not_found_only():
    """It must not be presented as proof of a version mismatch."""
    diag = _load()
    text = diag.decode_revert("0x90cb8b61")

    assert "not found in the consensus ABIs" in text

    for overclaim in ("mismatch", "does not describe", "stale", "proves"):
        assert overclaim not in text.lower()
