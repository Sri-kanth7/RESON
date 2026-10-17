"""Central configuration for RESON."""

from functools import lru_cache
from pathlib import Path
from typing import Literal
import os

from dotenv import load_dotenv
from pydantic import BaseModel, Field


PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env")

#: Storage backends selectable via ``RESON_STORAGE_BACKEND`` or the
#: ``storage_backend`` argument of ``create_application``.
SUPPORTED_STORAGE_BACKENDS = ("supabase", "memory")

#: Backend used when nothing is selected explicitly. Supabase remains the
#: production default; existing callers observe no behavior change.
DEFAULT_STORAGE_BACKEND = "supabase"


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

    storage_backend: Literal["supabase", "memory"] = DEFAULT_STORAGE_BACKEND

    supabase: SupabaseConfig | None = None

    simulation: SimulationConfig = SimulationConfig()

    default_lookback_hours: int = Field(default=24, ge=1)


def _resolve_backend(value: str | None) -> str:
    """Normalize and validate a storage backend selection.

    Raises a ``RuntimeError`` naming the supported backends when the value
    is not supported. Never falls back silently.
    """
    backend = (
        value
        if value is not None
        else os.getenv("RESON_STORAGE_BACKEND", DEFAULT_STORAGE_BACKEND)
    )
    normalized = backend.strip().lower()
    if normalized not in SUPPORTED_STORAGE_BACKENDS:
        raise RuntimeError(
            f"Unsupported storage backend {backend!r}. "
            f"Supported backends: {', '.join(SUPPORTED_STORAGE_BACKENDS)}. "
            "Set RESON_STORAGE_BACKEND to a supported value."
        )
    return normalized


def _load_config(storage_backend: str | None = None) -> RESONConfig:
    """Build the application configuration for an explicit backend.

    Supabase credentials are required only when the Supabase backend is
    selected; the ``memory`` backend loads without them.
    """
    backend = _resolve_backend(storage_backend)

    supabase: SupabaseConfig | None = None

    if backend == "supabase":
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_secret_key = os.getenv("SUPABASE_SECRET_KEY")

        if not supabase_url:
            raise RuntimeError(
                "SUPABASE_URL is not configured. It is required for the "
                "'supabase' storage backend."
            )

        if not supabase_secret_key:
            raise RuntimeError(
                "SUPABASE_SECRET_KEY is not configured. It is required for "
                "the 'supabase' storage backend."
            )

        supabase = SupabaseConfig(
            url=supabase_url,
            secret_key=supabase_secret_key,
        )

    return RESONConfig(
        environment=os.getenv(
            "RESON_ENVIRONMENT",
            "development",
        ),
        storage_backend=backend,  # type: ignore[arg-type]
        supabase=supabase,
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


@lru_cache(maxsize=1)
def get_config() -> RESONConfig:
    """Load and cache the application configuration."""

    return _load_config()