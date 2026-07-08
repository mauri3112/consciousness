from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel


class Settings(BaseModel):
    database_path: Path = Path("./data/consciousness.db")
    loop_interval_seconds: int = 60
    allow_procedure_mutation: bool = False
    only_memories_url: str | None = "http://localhost:8765"
    only_memories_write_recaps: bool = False
    api_host: str = "0.0.0.0"
    api_port: int = 8770


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_settings() -> Settings:
    return Settings(
        database_path=Path(os.getenv("CONSCIOUSNESS_DB", "./data/consciousness.db")),
        loop_interval_seconds=int(os.getenv("CONSCIOUSNESS_LOOP_INTERVAL_SECONDS", "60")),
        allow_procedure_mutation=_env_bool("CONSCIOUSNESS_ALLOW_PROCEDURE_MUTATION", False),
        only_memories_url=os.getenv("ONLY_MEMORIES_URL", "http://localhost:8765") or None,
        only_memories_write_recaps=_env_bool("ONLY_MEMORIES_WRITE_RECAPS", False),
        api_host=os.getenv("CONSCIOUSNESS_API_HOST", "0.0.0.0"),
        api_port=int(os.getenv("CONSCIOUSNESS_API_PORT", "8770")),
    )
