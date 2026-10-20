"""RESON application composition root."""

from dataclasses import dataclass

from src.common.config import (
    SUPPORTED_STORAGE_BACKENDS,
    RESONConfig,
    _load_config,
    get_config,
)
from src.data.ingestion import TelemetryIngestionService
from src.data.memory_storage import InMemoryStorage
from src.data.sqlite_storage import SQLiteStorage
from src.data.storage import Storage
from src.data.supabase_client import get_supabase_client
from src.data.supabase_storage import SupabaseStorage


@dataclass
class RESONApplication:
    """Fully configured RESON application."""

    config: RESONConfig
    storage: Storage
    ingestion: TelemetryIngestionService


def create_application(storage_backend: str | None = None) -> RESONApplication:
    """Construct the RESON application and its dependencies.

    ``storage_backend`` explicitly selects the storage implementation:
    ``"supabase"`` (the default, persistent cloud storage requiring
    credentials), ``"sqlite"`` (persistent local storage on the user's
    machine, no credentials or network required), or ``"memory"`` (temporary,
    non-persistent in-memory storage for tests and offline development). When
    omitted, the backend comes from the ``RESON_STORAGE_BACKEND`` environment
    variable, defaulting to ``"supabase"``. Unsupported values raise
    ``ValueError`` before anything is initialized; nothing ever falls back
    silently.
    """

    if storage_backend is None:
        config = get_config()
    else:
        normalized = storage_backend.strip().lower()
        if normalized not in SUPPORTED_STORAGE_BACKENDS:
            raise ValueError(
                f"Unsupported storage backend {storage_backend!r}. "
                f"Supported backends: {', '.join(SUPPORTED_STORAGE_BACKENDS)}."
            )
        config = _load_config(normalized)

    if config.storage_backend == "memory":
        storage: Storage = InMemoryStorage()
    elif config.storage_backend == "sqlite":
        storage = SQLiteStorage(config.sqlite_db_path)
    else:
        supabase = get_supabase_client()
        storage = SupabaseStorage(supabase)

    ingestion = TelemetryIngestionService(storage)

    return RESONApplication(
        config=config,
        storage=storage,
        ingestion=ingestion,
    )
