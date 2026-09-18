from src.common.config import get_config


def test_config_loads():
    config = get_config()

    assert config.supabase.url
    assert config.supabase.secret_key
    assert config.environment == "development"


def test_config_is_cached():
    first = get_config()
    second = get_config()

    assert first is second