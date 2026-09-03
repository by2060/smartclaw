from unittest.mock import AsyncMock, patch

import pytest

from smartclaw.session.runner import SessionRunner
from smartclaw.session.session import Session, SessionInfo


@pytest.mark.asyncio
async def test_resolve_output_session_id_caches_root_session():
    session = SessionInfo(
        id="ses_child",
        projectID="project-test",
        directory="/workspace/project-test",
    )
    runner = SessionRunner(
        session,
        provider_id="provider-test",
        model_id="model-test",
    )

    with patch.object(
        Session,
        "resolve_root_session_id",
        AsyncMock(return_value="ses_root"),
    ) as resolve_root:
        assert await runner._resolve_output_session_id() == "ses_root"
        assert await runner._resolve_output_session_id() == "ses_root"

    resolve_root.assert_awaited_once_with("ses_child")
    assert runner._main_session_key == "ses_root"
