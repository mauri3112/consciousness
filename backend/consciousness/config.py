from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field


class Settings(BaseModel):
    database_path: Path = Path("./data/consciousness.db")
    artifact_root: Path = Path("./data/artifacts")
    loop_interval_seconds: int = Field(default=60, ge=1)
    execution_mode: str = "preview"
    only_memories_url: str | None = "http://localhost:8765"
    only_memories_write_recaps: bool = False
    openai_api_key: str | None = None
    ollama_url: str = "http://localhost:11434"
    api_host: str = "127.0.0.1"
    api_port: int = 8770
    api_token: str | None = None
    allow_insecure_bind: bool = False
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:5174",
            "http://127.0.0.1:5174",
        ]
    )
    worker_poll_seconds: float = Field(default=1.0, gt=0)
    worker_lease_seconds: int = Field(default=30, ge=5)

    def validate_bind(self) -> None:
        if self.api_host not in {"127.0.0.1", "localhost", "::1"} and not self.api_token and not self.allow_insecure_bind:
            raise RuntimeError(
                "A non-loopback API bind requires CONSCIOUSNESS_API_TOKEN or explicit "
                "CONSCIOUSNESS_ALLOW_INSECURE_BIND=true behind a loopback-only port mapping."
            )


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_settings() -> Settings:
    origins = os.getenv("CONSCIOUSNESS_CORS_ORIGINS")
    return Settings(
        database_path=Path(os.getenv("CONSCIOUSNESS_DB", "./data/consciousness.db")),
        artifact_root=Path(os.getenv("CONSCIOUSNESS_ARTIFACT_ROOT", "./data/artifacts")),
        loop_interval_seconds=int(os.getenv("CONSCIOUSNESS_LOOP_INTERVAL_SECONDS", "60")),
        execution_mode=os.getenv("CONSCIOUSNESS_EXECUTION_MODE", "preview"),
        only_memories_url=os.getenv("ONLY_MEMORIES_URL", "http://localhost:8765") or None,
        only_memories_write_recaps=_env_bool("ONLY_MEMORIES_WRITE_RECAPS", False),
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        ollama_url=os.getenv("OLLAMA_URL", "http://localhost:11434"),
        api_host=os.getenv("CONSCIOUSNESS_API_HOST", "127.0.0.1"),
        api_port=int(os.getenv("CONSCIOUSNESS_API_PORT", "8770")),
        api_token=os.getenv("CONSCIOUSNESS_API_TOKEN") or None,
        allow_insecure_bind=_env_bool("CONSCIOUSNESS_ALLOW_INSECURE_BIND", False),
        cors_origins=[item.strip() for item in origins.split(",") if item.strip()] if origins else [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:5174",
            "http://127.0.0.1:5174",
        ],
        worker_poll_seconds=float(os.getenv("CONSCIOUSNESS_WORKER_POLL_SECONDS", "1")),
        worker_lease_seconds=int(os.getenv("CONSCIOUSNESS_WORKER_LEASE_SECONDS", "30")),
    )
