from __future__ import annotations

from typing import Any, Dict

_session_user_context: Dict[str, Dict[str, Any]] = {}


def set_session_user_context(session_id: str, user_context: Dict[str, Any]) -> None:
    _session_user_context[session_id] = dict(user_context)


def get_session_user_context(session_id: str) -> Dict[str, Any]:
    return dict(_session_user_context.get(session_id, {}))


def clear_session_user_context(session_id: str) -> None:
    _session_user_context.pop(session_id, None)
