from src.data.supabase_storage import SupabaseStorage
from src.orchestrator import RESONApplication, create_application


def test_create_application():
    application = create_application()

    assert isinstance(application, RESONApplication)
    assert application.config is not None
    assert isinstance(application.storage, SupabaseStorage)
