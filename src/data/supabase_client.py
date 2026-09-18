"""Supabase client factory for RESON."""

from functools import lru_cache

from supabase import Client, create_client

from src.common.config import get_config


@lru_cache(maxsize=1)
def get_supabase_client() -> Client:
    """Create and cache the application's Supabase client."""

    config = get_config()

    return create_client(
        config.supabase.url,
        config.supabase.secret_key,
    )