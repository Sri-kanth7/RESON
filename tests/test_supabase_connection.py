from tests.conftest import requires_supabase

from src.data.supabase_client import get_supabase_client

# Every test in this module is a live integration test against a reachable
# Supabase project. The marker skips them (with an explicit reason) when the
# credentials are not configured; the assertions are unchanged.
pytestmark = requires_supabase


def test_supabase_connection():
    client = get_supabase_client()

    response = client.table("services").select("id").limit(1).execute()

    assert response.data is not None