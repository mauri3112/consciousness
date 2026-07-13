from cryptography.fernet import Fernet
import pytest

from consciousness.credentials import CredentialStore


def test_write_only_vault_encrypts_and_resolves_by_reference(tmp_path):
    path = tmp_path / "credentials.enc"
    store = CredentialStore(path, Fernet.generate_key().decode())

    store.put("vault:minimax/MiniMax-M3", "sk-cp-secret")

    assert store.get("vault:minimax/MiniMax-M3") == "sk-cp-secret"
    assert b"sk-cp-secret" not in path.read_bytes()
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_vault_refuses_ui_keys_without_master_key(tmp_path):
    store = CredentialStore(tmp_path / "credentials.enc", None)

    with pytest.raises(RuntimeError, match="CONSCIOUSNESS_CREDENTIAL_KEY"):
        store.put("vault:model", "secret")
