# srag/ingestion/embedder.py
from __future__ import annotations
from typing import Generator
import ollama

BATCH_SIZE = 32

# Some embedding models are trained asymmetrically and expect different
# prompt prefixes for queries vs. documents; using the wrong one (or none,
# for a model that wants one) measurably hurts retrieval quality. Keyed on
# the model name with any Ollama ":tag" suffix stripped. Unknown models
# fall back to no prefix, matching prior behavior.
_QUERY_PREFIXES = {
    "nomic-embed-text": "search_query: ",
    "embeddinggemma": "task: search result | query: ",
}
_DOCUMENT_PREFIXES = {
    "nomic-embed-text": "search_document: ",
    "embeddinggemma": "title: none | text: ",
}


def _prefix_for(model: str, table: dict[str, str]) -> str:
    base = model.split(":", 1)[0]
    return table.get(base, "")


def embed_texts(texts: list[str], model: str) -> list[list[float]]:
    prefix = _prefix_for(model, _DOCUMENT_PREFIXES)
    embeddings: list[list[float]] = []
    for batch in _batches(texts, BATCH_SIZE):
        for text in batch:
            resp = ollama.embeddings(model=model, prompt=prefix + text)
            embeddings.append(resp["embedding"])
    return embeddings


def embed_query(text: str, model: str) -> list[float]:
    prefix = _prefix_for(model, _QUERY_PREFIXES)
    resp = ollama.embeddings(model=model, prompt=prefix + text)
    return resp["embedding"]


def _batches(items: list, size: int) -> Generator:
    for i in range(0, len(items), size):
        yield items[i : i + size]
