"""RESON application composition root."""

from dataclasses import dataclass

from src.common.config import RESONConfig, get_config
from src.data.supabase_client import get_supabase_client


@dataclass
class RESONApplication:
    """Fully configured RESON application."""

    config: RESONConfig
    supabase: object


def create_application() -> RESONApplication:
    """Construct the RESON application and its dependencies."""

    config = get_config()
    supabase = get_supabase_client()

    return RESONApplication(
        config=config,
        supabase=supabase,
    )