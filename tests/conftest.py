# tests/conftest.py
import pytest
from srag.store.db import init_db

@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test.sqlite")
    init_db(path)
    return path

@pytest.fixture
def fake_embedding():
    return [0.1] * 768

@pytest.fixture
def fake_embedding_fn(fake_embedding):
    return lambda text, model: fake_embedding
