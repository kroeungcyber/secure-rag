# srag/store/models.py
from __future__ import annotations
import json
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class Document:
    id: str
    source_path: str
    title: str
    file_type: str
    ingested_at: str
    chunk_count: int
    mtime: float
    embed_model: str = ""

@dataclass
class Chunk:
    id: Optional[int]
    doc_id: str
    content: str
    chunk_index: int
    metadata: dict = field(default_factory=dict)

    def metadata_json(self) -> str:
        return json.dumps(self.metadata)

    @classmethod
    def from_row(cls, row: tuple) -> Chunk:
        id_, doc_id, content, chunk_index, metadata_str = row
        return cls(
            id=id_,
            doc_id=doc_id,
            content=content,
            chunk_index=chunk_index,
            metadata=json.loads(metadata_str or "{}"),
        )
