"""API-key matching and the auth-related CLI commands (agent.md §7.8, E30)."""

from __future__ import annotations

import pytest

from app.cli.main import build_parser, main
from app.services.auth import api_key_matches, hash_token


def test_hash_token_is_sha256_hex() -> None:
    assert hash_token("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_api_key_matching() -> None:
    digest = hash_token("key-1")
    assert api_key_matches("key-1", [hash_token("other"), digest])
    assert api_key_matches("key-1", [f"  {digest.upper()}  "])  # tolerant of copy/paste case
    assert not api_key_matches("key-2", [digest])
    assert not api_key_matches("key-1", [])
    assert not api_key_matches("", [digest])


def test_cli_has_no_secret_arguments() -> None:
    parser = build_parser()
    admin = parser.parse_args(["create-admin", "--username", "root", "--email", "r@example.org"])
    assert admin.command == "create-admin" and admin.password_stdin is False
    assert not hasattr(admin, "password")  # passwords never travel through argv
    with pytest.raises(SystemExit):
        parser.parse_args(["create-admin", "--username", "root", "--email", "r@x.org", "--password", "p"])
    assert parser.parse_args(["hash-api-key", "--generate"]).generate is True


def test_hash_api_key_generate_prints_key_and_matching_digest(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["hash-api-key", "--generate"]) == 0
    out = capsys.readouterr().out
    key = out.split("shown once): ")[1].splitlines()[0]
    digest = out.split("digest: ")[1].strip()
    assert len(key) >= 40 and digest == hash_token(key)
