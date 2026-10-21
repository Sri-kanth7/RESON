"""Tests for explicit storage selection (offline except where noted).

Supabase remains the default backend. The ``memory`` backend must work
without Supabase credentials and without initializing a Supabase client.
Invalid selections must fail loudly; nothing falls back silently.
"""

import pytest

from tests.conftest import requires_supabase

from src.common.config import get_config
from src.data.ingestion import TelemetryIngestionService
from src.data.memory_storage import InMemoryStorage
from src.data.supabase_client import get_supabase_client
from src.data.supabase_storage import SupabaseStorage
from src.orchestrator import RESONApplication, create_application


@pytest.fixture
def fresh_config(monkeypatch):
    """Isolate cached configuration and client factory from env edits."""
    get_config.cache_clear()
    get_supabase_client.cache_clear()
    yield monkeypatch
    get_config.cache_clear()
    get_supabase_client.cache_clear()


def drop_supabase_creds(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)


def forbid_supabase_client(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("Supabase client must not initialize in local mode")

    monkeypatch.setattr(
        "src.orchestrator.get_supabase_client", explode
    )


@requires_supabase
def test_default_backend_is_supabase(fresh_config):
    monkeypatch = fresh_config
    monkeypatch.delenv("RESON_STORAGE_BACKEND", raising=False)
    config = get_config()
    assert config.storage_backend == "supabase"
    assert config.supabase is not None


@requires_supabase
def test_create_application_defaults_to_supabase(fresh_config):
    monkeypatch = fresh_config
    monkeypatch.delenv("RESON_STORAGE_BACKEND", raising=False)
    application = create_application()
    assert isinstance(application, RESONApplication)
    assert application.config.storage_backend == "supabase"
    assert isinstance(application.storage, SupabaseStorage)
    assert isinstance(application.ingestion, TelemetryIngestionService)
    assert application.ingestion._storage is application.storage


def test_memory_backend_via_environment(fresh_config):
    monkeypatch = fresh_config
    monkeypatch.setenv("RESON_STORAGE_BACKEND", "memory")
    drop_supabase_creds(monkeypatch)
    forbid_supabase_client(monkeypatch)

    application = create_application()

    assert application.config.storage_backend == "memory"
    assert application.config.supabase is None
    assert isinstance(application.storage, InMemoryStorage)
    assert isinstance(application.ingestion, TelemetryIngestionService)
    assert application.ingestion._storage is application.storage


def test_memory_backend_via_explicit_argument(fresh_config):
    monkeypatch = fresh_config
    monkeypatch.delenv("RESON_STORAGE_BACKEND", raising=False)
    drop_supabase_creds(monkeypatch)
    forbid_supabase_client(monkeypatch)

    application = create_application("memory")

    assert application.config.storage_backend == "memory"
    assert isinstance(application.storage, InMemoryStorage)


def test_memory_backend_accepts_case_and_whitespace(fresh_config):
    monkeypatch = fresh_config
    drop_supabase_creds(monkeypatch)
    forbid_supabase_client(monkeypatch)

    assert create_application("  Memory ").config.storage_backend == "memory"


def test_memory_instances_are_isolated(fresh_config):
    monkeypatch = fresh_config
    drop_supabase_creds(monkeypatch)
    forbid_supabase_client(monkeypatch)

    first = create_application("memory")
    second = create_application("memory")

    assert first.storage is not second.storage


def test_invalid_backend_argument_raises_value_error(fresh_config):
    fresh_config.delenv("RESON_STORAGE_BACKEND", raising=False)
    with pytest.raises(ValueError, match="Unsupported storage backend"):
        create_application("postgres")


def test_invalid_backend_environment_raises(fresh_config):
    monkeypatch = fresh_config
    monkeypatch.setenv("RESON_STORAGE_BACKEND", "postgres")
    with pytest.raises(RuntimeError, match="Unsupported storage backend"):
        get_config()
    with pytest.raises(RuntimeError, match="Unsupported storage backend"):
        create_application()


def test_supabase_backend_requires_credentials(fresh_config):
    monkeypatch = fresh_config
    monkeypatch.setenv("RESON_STORAGE_BACKEND", "supabase")
    drop_supabase_creds(monkeypatch)
    with pytest.raises(RuntimeError, match="SUPABASE_URL"):
        create_application()


def test_supabase_client_without_configuration_raises(fresh_config):
    monkeypatch = fresh_config
    monkeypatch.setenv("RESON_STORAGE_BACKEND", "memory")
    drop_supabase_creds(monkeypatch)
    with pytest.raises(RuntimeError, match="not configured"):
        get_supabase_client()


def test_memory_config_loads_without_credentials(fresh_config):
    monkeypatch = fresh_config
    monkeypatch.setenv("RESON_STORAGE_BACKEND", "memory")
    drop_supabase_creds(monkeypatch)
    config = get_config()
    assert config.storage_backend == "memory"
    assert config.supabase is None
    assert config.environment == "development"
