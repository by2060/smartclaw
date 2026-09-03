from smartclaw.server.routes import session as session_route
from smartclaw.sandbox.uploads import UPLOADS_CHAT_PREFIX


def test_session_route_uses_shared_chat_upload_prefix():
    assert session_route.UPLOADS_CHAT_PREFIX == UPLOADS_CHAT_PREFIX
