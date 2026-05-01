"""Logger secret-redaction policy."""

from __future__ import annotations

from polybot.obs.logger import _PRIVKEY_RE, safe_repr


def test_safe_repr_redacts_64hex_string() -> None:
    privkey = "0x" + "1" * 64
    s = safe_repr({"key": privkey})
    assert privkey not in s
    assert "PRIVKEY-REDACTED" in s


def test_safe_repr_redacts_known_field_names() -> None:
    out = safe_repr({"private_key": "anything", "api_secret": "shh", "ok": "fine"})
    assert "anything" not in out
    assert "shh" not in out
    assert "fine" in out


def test_privkey_re_matches_only_full_length() -> None:
    assert _PRIVKEY_RE.search("0x" + "1" * 64) is not None
    # 63 hex chars -> not a privkey
    assert _PRIVKEY_RE.search("0x" + "1" * 63) is None
