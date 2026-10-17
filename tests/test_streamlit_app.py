"""Streamlit shell tests via AppTest (offline, memory backend)."""

import pytest
from streamlit.testing.v1 import AppTest

from src.common.config import get_config
from src.data.supabase_client import get_supabase_client

APP = "../app/streamlit_app.py"


@pytest.fixture
def no_credentials(monkeypatch):
    monkeypatch.delenv("RESON_STORAGE_BACKEND", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    get_config.cache_clear()
    get_supabase_client.cache_clear()
    yield
    get_config.cache_clear()
    get_supabase_client.cache_clear()


def run_app():
    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    assert not at.exception, at.exception
    return at


def test_default_backend_without_credentials_shows_guidance(no_credentials):
    at = run_app()
    assert any(
        "Select a working storage backend" in str(m.value) for m in at.markdown
    )


def test_memory_mode_first_run_state(no_credentials):
    at = run_app()
    at.selectbox[0].set_value("memory").run()
    assert not at.exception, at.exception
    assert any(
        "storage is empty" in str(m.value).lower() for m in at.markdown
    )


def test_navigation_reaches_all_pages(no_credentials):
    at = run_app()
    at.selectbox[0].set_value("memory").run()
    for page in (
        "Incidents",
        "Behavior Analytics",
        "Historical Explorer",
    ):
        at.radio[0].set_value(page).run()
        assert not at.exception, at.exception
    at.radio[0].set_value("Overview").run()
    assert not at.exception, at.exception


def test_demonstration_button_produces_registry(no_credentials):
    at = run_app()
    at.selectbox[0].set_value("memory").run()
    demo_buttons = [b for b in at.button if "Demonstration" in str(b.label)]
    assert demo_buttons
    demo_buttons[0].click().run()
    assert not at.exception, at.exception
    assert any(
        "Correlated Incident Registry" in str(m.value) for m in at.markdown
    )


def test_unreachable_backend_shows_guidance(no_credentials, monkeypatch):
    from unittest import mock

    with mock.patch(
        "app.services.backend.storage_counts",
        side_effect=RuntimeError("simulated outage"),
    ):
        at = run_app()
        at.selectbox[0].set_value("memory").run()
        assert not at.exception, at.exception
        texts = [str(m.value) for m in at.markdown] + [
            str(e.value) for e in at.error
        ]
        assert any("Backend read failed" in t for t in texts)


def test_all_pages_render_after_demonstration(no_credentials):
    at = run_app()
    at.selectbox[0].set_value("memory").run()
    demo_buttons = [b for b in at.button if "Demonstration" in str(b.label)]
    demo_buttons[0].click().run()
    assert not at.exception, at.exception
    for page in (
        "Overview",
        "Incidents",
        "Behavior Analytics",
        "Historical Explorer",
    ):
        at.radio[0].set_value(page).run()
        assert not at.exception, (page, at.exception)

