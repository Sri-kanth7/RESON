"""Central configuration for RESON."""

from functools import lru_cache
from pathlib import Path
import os

from dotenv import load_dotenv
from pydantic import BaseModel, Field


PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env")


class SupabaseConfig(BaseModel):
    """Configuration required to connect to Supabase."""

    url: str = Field(min_length=1)
    secret_key: str = Field(min_length=1)


class SimulationConfig(BaseModel):
    """Configuration for the deterministic telemetry simulator."""

    seed: int = 42
    interval_seconds: int = Field(default=10, ge=1)


class RESONConfig(BaseModel):
    """Application-wide RESON configuration."""

    environment: str = "development"

    supabase: SupabaseConfig

    simulation: SimulationConfig = SimulationConfig()

    default_lookback_hours: int = Field(default=24, ge=1)


@lru_cache(maxsize=1)
def get_config() -> RESONConfig:
    """Load and cache the application configuration."""

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_secret_key = os.getenv("SUPABASE_SECRET_KEY")

    if not supabase_url:
        raise RuntimeError(
            "SUPABASE_URL is not configured."
        )

    if not supabase_secret_key:
        raise RuntimeError(
            "SUPABASE_SECRET_KEY is not configured."
        )

    return RESONConfig(
        environment=os.getenv(
            "RESON_ENVIRONMENT",
            "development",
        ),
        supabase=SupabaseConfig(
            url=supabase_url,
            secret_key=supabase_secret_key,
        ),
        simulation=SimulationConfig(
            seed=int(
                os.getenv(
                    "RESON_SIMULATION_SEED",
                    "42",
                )
            ),
            interval_seconds=int(
                os.getenv(
                    "RESON_SIMULATION_INTERVAL",
                    "10",
                )
            ),
        ),
        default_lookback_hours=int(
            os.getenv(
                "RESON_DEFAULT_LOOKBACK_HOURS",
                "24",
            )
        ),
    )