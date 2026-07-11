from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .models import ArtifactPointer, ArtifactRecord
from .store import ConsciousnessStore, make_id, utcnow


class ArtifactStore:
    def __init__(self, root: Path, store: ConsciousnessStore) -> None:
        self.root = root.resolve()
        self.store = store

    def write_text(self, run_id: str, filename: str, content: str, *, label: str, kind: str = "text/markdown") -> ArtifactPointer:
        safe_name = Path(filename).name
        run_root = (self.root / run_id).resolve()
        run_root.mkdir(parents=True, exist_ok=True)
        path = (run_root / safe_name).resolve()
        if self.root not in path.parents:
            raise ValueError("artifact path escapes configured root")
        data = content.encode("utf-8")
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(data)
        os.replace(temporary, path)
        digest = hashlib.sha256(data).hexdigest()
        record = ArtifactRecord(
            id=make_id("artifact"),
            run_id=run_id,
            label=label,
            kind=kind,
            uri=f"artifact://{run_id}/{safe_name}",
            path=str(path),
            content_hash=digest,
            mime_type=kind,
            size_bytes=len(data),
            created_at=utcnow(),
        )
        self.store.add_artifact(record)
        self.store.add_event("artifact.written", {"artifact_id": record.id, "uri": record.uri}, run_id=run_id)
        return ArtifactPointer(label=label, kind=kind, uri=record.uri, content_hash=digest)

    def resolve(self, run_id: str, filename: str) -> Path:
        path = (self.root / run_id / Path(filename).name).resolve()
        if self.root not in path.parents or not path.is_file():
            raise FileNotFoundError(filename)
        return path
