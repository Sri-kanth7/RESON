from tests.conftest import requires_supabase

from src.common.config import get_config


@requires_supabase
def test_config_loads():
    config = get_config()

    assert config.supabase.url
    assert config.supabase.secret_key
    assert config.environment == "development"


@requires_supabase
def test_config_is_cached():
    first = get_config()
    second = get_config()

    assert first is second
