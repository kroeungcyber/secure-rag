# tests/test_embedder.py
from srag.ingestion.embedder import embed_texts, embed_query

def test_embed_texts_returns_one_vector_per_text(mocker):
    mock = mocker.patch("srag.ingestion.embedder.ollama.embeddings")
    mock.return_value = {"embedding": [0.5] * 768}

    results = embed_texts(["hello", "world"], model="nomic-embed-text")

    assert len(results) == 2
    assert len(results[0]) == 768
    assert mock.call_count == 2

def test_embed_query_returns_single_vector(mocker):
    mock = mocker.patch("srag.ingestion.embedder.ollama.embeddings")
    mock.return_value = {"embedding": [0.1] * 768}

    vec = embed_query("what is nginx", model="nomic-embed-text")

    assert len(vec) == 768
    mock.assert_called_once_with(model="nomic-embed-text", prompt="search_query: what is nginx")


def test_embed_query_applies_model_specific_prefix(mocker):
    mock = mocker.patch("srag.ingestion.embedder.ollama.embeddings")
    mock.return_value = {"embedding": [0.1] * 768}

    embed_query("what is nginx", model="embeddinggemma:latest")

    mock.assert_called_once_with(
        model="embeddinggemma:latest",
        prompt="task: search result | query: what is nginx",
    )


def test_embed_texts_applies_document_prefix(mocker):
    mock = mocker.patch("srag.ingestion.embedder.ollama.embeddings")
    mock.return_value = {"embedding": [0.1] * 768}

    embed_texts(["restart nginx"], model="nomic-embed-text")

    mock.assert_called_once_with(model="nomic-embed-text", prompt="search_document: restart nginx")


def test_embed_query_unknown_model_has_no_prefix(mocker):
    mock = mocker.patch("srag.ingestion.embedder.ollama.embeddings")
    mock.return_value = {"embedding": [0.1] * 768}

    embed_query("what is nginx", model="llama3.1:8b")

    mock.assert_called_once_with(model="llama3.1:8b", prompt="what is nginx")

def test_embed_texts_batches_correctly(mocker):
    mock = mocker.patch("srag.ingestion.embedder.ollama.embeddings")
    mock.return_value = {"embedding": [0.1] * 768}

    texts = [f"text {i}" for i in range(70)]
    results = embed_texts(texts, model="nomic-embed-text")

    assert len(results) == 70
    # 70 texts / batch_size=32 = 3 batches → 70 individual calls
    assert mock.call_count == 70
