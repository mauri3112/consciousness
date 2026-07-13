from __future__ import annotations

import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class CredentialStore:
    """Small write-only local vault; procedure rows contain references, never secrets."""

    def __init__(self, path: Path, encryption_key: str | None) -> None:
        self.path = path
        self._fernet = Fernet(encryption_key.encode()) if encryption_key else None

    @property
    def writable(self) -> bool:
        return self._fernet is not None

    def put(self, reference: str, value: str) -> None:
        if not self._fernet:
            raise RuntimeError("CONSCIOUSNESS_CREDENTIAL_KEY is required for UI-entered keys")
        if not value.strip():
            raise ValueError("credential must not be empty")
        values = self._read()
        values[reference] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(self._fernet.encrypt(json.dumps(values).encode()))
        os.chmod(self.path, 0o600)

    def get(self, reference: str) -> str | None:
        return self._read().get(reference)

    def configured(self, reference: str) -> bool:
        return bool(self.get(reference))

    def _read(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        if not self._fernet:
            return {}
        try:
            payload = json.loads(self._fernet.decrypt(self.path.read_bytes()))
        except (InvalidToken, json.JSONDecodeError) as exc:
            raise RuntimeError("credential vault cannot be decrypted with the configured key") from exc
        return {str(key): str(value) for key, value in payload.items()}
