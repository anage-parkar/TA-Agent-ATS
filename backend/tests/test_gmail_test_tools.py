"""The Gmail test-mailbox tools: env-file upsert, the verifier's hard guard,
the message under test, and a static check that neither script can print a
token. No network, no real credentials."""

from __future__ import annotations

import importlib.util
import pathlib
import re

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


envfile = _load("_envfile")
verifier = _load("verify_gmail_message_id")

TEST = {
    "GMAIL_TEST_ADDRESS": "ta-test@example.com",
    "GMAIL_TEST_CLIENT_ID": "test-client",
    "GMAIL_TEST_CLIENT_SECRET": "s",
    "GMAIL_TEST_REFRESH_TOKEN": "r",
}


# ── env file ──────────────────────────────────────────────────────────
def test_upsert_creates_then_updates_only_the_one_key(tmp_path):
    path = tmp_path / "secrets" / "gmail-test.env"
    envfile.upsert_env_value(path, "GMAIL_TEST_REFRESH_TOKEN", "first")
    assert path.read_text() == "GMAIL_TEST_REFRESH_TOKEN=first\n"

    path.write_text("# test mailbox\nGMAIL_TEST_ADDRESS=ta-test@example.com\n"
                    "GMAIL_TEST_CLIENT_ID=abc\nGMAIL_TEST_REFRESH_TOKEN=first\n"
                    "GMAIL_TEST_REFRESH_TOKEN=dup\nGMAIL_TEST_CLIENT_SECRET='q'\n")
    envfile.upsert_env_value(path, "GMAIL_TEST_REFRESH_TOKEN", "second")
    text = path.read_text()
    assert text.count("GMAIL_TEST_REFRESH_TOKEN=") == 1          # duplicates collapsed
    assert "GMAIL_TEST_REFRESH_TOKEN=second" in text
    assert "# test mailbox" in text and "GMAIL_TEST_CLIENT_ID=abc" in text  # everything else kept
    assert text.index("GMAIL_TEST_ADDRESS") < text.index("GMAIL_TEST_REFRESH_TOKEN")  # order kept
    assert envfile.read_env_file(path) == {
        "GMAIL_TEST_ADDRESS": "ta-test@example.com", "GMAIL_TEST_CLIENT_ID": "abc",
        "GMAIL_TEST_REFRESH_TOKEN": "second", "GMAIL_TEST_CLIENT_SECRET": "q",
    }


def test_read_env_file_tolerates_missing_file_comments_and_quotes(tmp_path):
    assert envfile.read_env_file(tmp_path / "nope.env") == {}
    p = tmp_path / "x.env"
    p.write_text('# c\n\nA=1\nB="two words"\nnot a pair\nC = 3 \n')
    assert envfile.read_env_file(p) == {"A": "1", "B": "two words", "C": "3"}


# ── the verifier's hard guard ─────────────────────────────────────────
def test_guard_passes_for_test_mailbox_to_itself_with_separate_credentials():
    verifier.guard(TEST, "recruiting@parkar.in", "live-client", TEST["GMAIL_TEST_ADDRESS"], TEST["GMAIL_TEST_ADDRESS"])


@pytest.mark.parametrize("sender, recipient", [
    ("recruiting@parkar.in", "ta-test@example.com"),   # live sender
    ("ta-test@example.com", "candidate@x.com"),        # a real recipient
    ("TA-Test@example.com", "other@example.com"),
])
def test_guard_refuses_any_sender_or_recipient_other_than_the_test_mailbox(sender, recipient):
    with pytest.raises(verifier.GuardRefused, match="sender and recipient"):
        verifier.guard(TEST, "recruiting@parkar.in", "live-client", sender, recipient)


def test_guard_refuses_when_the_test_mailbox_is_the_live_mailbox():
    with pytest.raises(verifier.GuardRefused, match="live OUTREACH_FROM_EMAIL"):
        verifier.guard(TEST, "TA-TEST@example.com", "live-client", TEST["GMAIL_TEST_ADDRESS"], TEST["GMAIL_TEST_ADDRESS"])


def test_guard_refuses_when_the_oauth_client_is_the_live_client():
    with pytest.raises(verifier.GuardRefused, match="live GMAIL_CLIENT_ID"):
        verifier.guard(TEST, "recruiting@parkar.in", "test-client", TEST["GMAIL_TEST_ADDRESS"], TEST["GMAIL_TEST_ADDRESS"])


def test_guard_refuses_incomplete_credentials():
    partial = {k: v for k, v in TEST.items() if k != "GMAIL_TEST_REFRESH_TOKEN"}
    with pytest.raises(verifier.GuardRefused, match="missing"):
        verifier.guard(partial, "recruiting@parkar.in", "live-client", TEST["GMAIL_TEST_ADDRESS"], TEST["GMAIL_TEST_ADDRESS"])


def test_guard_is_sensitive_to_its_own_removal(monkeypatch):
    """Sensitivity entry (same-PR rule): with the guard disabled, the refusal
    test above would not raise. Folded into tests/test_safety_sensitivity.py
    once #14 is in main."""
    monkeypatch.setattr(verifier, "guard", lambda *a, **k: None)
    with pytest.raises(pytest.fail.Exception):
        with pytest.raises(verifier.GuardRefused):
            verifier.guard(TEST, "TA-TEST@example.com", "live-client", TEST["GMAIL_TEST_ADDRESS"], TEST["GMAIL_TEST_ADDRESS"])


# ── the message under test ────────────────────────────────────────────
def test_message_carries_both_headers_and_is_self_addressed():
    msg, raw = verifier.build_message("ta-test@example.com", "ta-test@example.com",
                                      "<ta-abc@example.com>", "idem:test:abc")
    assert msg["From"] == msg["To"] == "ta-test@example.com"
    assert msg["Message-ID"] == "<ta-abc@example.com>"
    assert msg["X-TA-Idempotency-Key"] == "idem:test:abc"
    assert raw and "=" not in raw.rstrip("=")  # base64url body


# ── neither script may print a token ──────────────────────────────────
@pytest.mark.parametrize("script", ["gmail_test_auth.py", "verify_gmail_message_id.py"])
def test_scripts_never_print_tokens(script):
    src = (SCRIPTS / script).read_text(encoding="utf-8")
    for line in src.splitlines():
        if "print(" in line:
            assert not re.search(r"refresh_token|creds\.token|CLIENT_SECRET\b.*\{|\{.*secret", line), line
