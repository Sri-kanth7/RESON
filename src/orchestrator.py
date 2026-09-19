"""RESON application composition root."""

from dataclasses import dataclass

from src.common.config import RESONConfig, get_config
from src.data.storage import Storage
from src.data.supabase_client import get_supabase_client
from src.data.supabase_storage import SupabaseStorage


@dataclass
class RESONApplication:
    """Fully configured RESON application."""

    config: RESONConfig
    storage: Storage


def create_application() -> RESONApplication:
    """Construct the RESON application and its dependencies."""

    config = get_config()
    supabase = get_supabase_client()
    storage = SupabaseStorage(supabase)

    return RESONApplication(
        config=config,
        storage=storage,
    )
