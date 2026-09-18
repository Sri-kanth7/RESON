from src.data.supabase_client import get_supabase_client


def test_supabase_connection():
    client = get_supabase_client()

    response = client.table("services").select("id").limit(1).execute()

    assert response.data is not None