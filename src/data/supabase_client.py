"""Supabase client factory for RESON."""

from functools import lru_cache

from supabase import Client, create_client

from src.common.config import get_config


@lru_cache(maxsize=1)
def get_supabase_client() -> Client:
    """Create and cache the application's Supabase client."""

    config = get_config()

    if config.supabase is None:
        raise RuntimeError(
            "Supabase client requested but Supabase is not configured. "
            "Set SUPABASE_URL and SUPABASE_SECRET_KEY, or select the "
            "'memory' storage backend for local use."
        )

    return create_client(
        config.supabase.url,
        config.supabase.secret_key,
    )