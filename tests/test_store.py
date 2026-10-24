from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.fernet import Fernet, InvalidToken

from line_agent.store import Store


def test_private_records_survive_restart_without_plaintext(tmp_path):
    key = Fernet.generate_key()
    store = Store(tmp_path / "one", key)
    store.put("contact", "u-personal-id", {"name": "機密の友達", "text": "秘密の会話"})
    assert Store(tmp_path / "one", key).get("contact", "u-personal-id")["name"] == "機密の友達"
    for file in (tmp_path / "one").iterdir():
        if file.is_file():
            content = file.read_bytes()
            assert b"u-personal-id" not in content
            assert "秘密の会話".encode() not in content


def test_accounts_and_keys_are_isolated(tmp_path):
    one = Store(tmp_path / "one", Fernet.generate_key())
    two = Store(tmp_path / "two", Fernet.generate_key())
    one.put("memory", "same", {"private": True})
    assert two.get("memory", "same") is None
    wrong = Store(tmp_path / "one", Fernet.generate_key())
    with pytest.raises(InvalidToken):
        wrong.records("memory")


def test_atomic_claim_allows_only_one_sender(tmp_path):
    store = Store(tmp_path / "one", Fernet.generate_key())
    store.put("action", "id", {"state": "authorized"})

    def claim(_):
        return store.transition("action", "id", "authorized", "sending")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(claim, range(8)))
    assert sum(r is not None for r in results) == 1


def test_symlink_account_directory_is_rejected(tmp_path):
    (tmp_path / "target").mkdir()
    (tmp_path / "alias").symlink_to(tmp_path / "target")
    with pytest.raises(ValueError):
        Store(tmp_path / "alias", Fernet.generate_key())


def test_legacy_messages_gain_scope_without_losing_evidence(tmp_path):
    key = Fernet.generate_key()
    store = Store(tmp_path / "one", key)
    store.put("message", "u1:message", {"chat": "u1", "id": "message", "text": "legacy"})
    assert store.count("message", scope="u1") == 0
    reopened = Store(tmp_path / "one", key)
    assert reopened.count("message", scope="u1") == 1
    assert reopened.records("message", scope="u1")[0]["value"]["text"] == "legacy"
