"""Phase 6: the deployment-preparation scripts.

The milestones file is written on Windows, where PowerShell's
`Set-Content -Encoding utf8` prepends a UTF-8 byte-order mark. json.loads
rejects that with "Unexpected UTF-8 BOM", so the reader must tolerate it.

These tests also pin the property the CLI broke: amounts stay decimal
strings all the way to the constructor argument, never becoming numbers.
"""

import json

import pytest

from scripts.prepare_deploy import read_milestones_file, validate_milestones

MILESTONES = [
    {"spec": "Unfunded runtime smoke test", "amount": "1"},
    {"spec": "Second milestone", "amount": "1000"},
]

BODY = json.dumps(MILESTONES, separators=(",", ":"))


def _write(tmp_path, text, encoding="utf-8", name="milestones.json"):
    path = tmp_path / name
    path.write_bytes(text.encode(encoding))

    return str(path)


def test_a_bom_prefixed_file_is_read(tmp_path):
    """What PowerShell's Set-Content -Encoding utf8 actually produces."""
    path = _write(tmp_path, BODY, encoding="utf-8-sig")

    raw = open(path, "rb").read()

    assert raw[:3] == b"\xef\xbb\xbf", "fixture must really carry a BOM"

    milestones, total = validate_milestones(read_milestones_file(path))

    assert len(milestones) == 2
    assert total == 1001


def test_a_plain_utf8_file_still_works(tmp_path):
    path = _write(tmp_path, BODY)

    assert open(path, "rb").read()[:3] != b"\xef\xbb\xbf"

    milestones, total = validate_milestones(read_milestones_file(path))

    assert total == 1001


def test_amounts_stay_decimal_strings_through_the_whole_path(tmp_path):
    """The exact corruption the CLI introduced: "1000" becoming 1000."""
    path = _write(tmp_path, BODY, encoding="utf-8-sig")

    milestones, _ = validate_milestones(read_milestones_file(path))

    for milestone in milestones:
        assert isinstance(milestone["amount"], str)

    # And the argument handed to the constructor is a string containing
    # quoted amounts, not a structure.
    argument = json.dumps(milestones, separators=(",", ":"))

    assert isinstance(argument, str)
    assert '"amount":"1000"' in argument
    assert '"amount":1000' not in argument


def test_a_numeric_amount_is_rejected(tmp_path):
    path = _write(
        tmp_path,
        json.dumps([{"spec": "x" * 20, "amount": 1000}]),
        encoding="utf-8-sig",
    )

    with pytest.raises(SystemExit):
        validate_milestones(read_milestones_file(path))


def test_bom_does_not_hide_malformed_json(tmp_path):
    """Stripping the BOM must not make bad JSON look acceptable."""
    path = _write(tmp_path, "[{'spec': 'single quotes'}]", encoding="utf-8-sig")

    with pytest.raises(SystemExit):
        validate_milestones(read_milestones_file(path))


def test_a_utf16_file_fails_loudly(tmp_path):
    """PowerShell 5.1's bare -Encoding unicode writes UTF-16; that is not
    silently accepted."""
    path = tmp_path / "utf16.json"
    path.write_bytes(BODY.encode("utf-16"))

    with pytest.raises((SystemExit, UnicodeDecodeError, UnicodeError)):
        validate_milestones(read_milestones_file(str(path)))
