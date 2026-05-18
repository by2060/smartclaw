"""
Remembered answers for the question tool.

This module stores deterministic question/answer preferences per local account
so repeated clarifying questions can be answered without interrupting the user.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any, Iterable, Optional, cast

from flocks.storage import Storage
from flocks.utils.log import Log

log = Log.create(service="memory.question")

_KEY_PREFIX = "question_memory"
_MEMORY_PATH = "question_answers.md"
_UNSAFE_TYPES = {"password", "file"}
_SENSITIVE_PROMPT_RE = re.compile(
    r"(password|passwd|pwd|token|secret|api[\s_-]*key|apikey|access[\s_-]*key|"
    r"auth|authorization|credential|bearer|认证|鉴权|授权|密钥|秘钥|口令|令牌|凭证)",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", re.UNICODE)
_GENERIC_SEMANTIC_TOKENS = {
    "api", "url", "uri", "endpoint", "base", "host", "http", "https",
    "configuration", "config", "task", "user", "the", "for", "used", "enter",
    "please", "service", "接口", "服务", "地址", "路径", "配置", "填写", "输入",
}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def normalize_remember_flags(
    remember: Any,
    count: int,
) -> list[bool]:
    """Normalize API remember payloads to one boolean per question."""
    if count <= 0:
        return []
    if remember is None:
        return [False] * count
    if isinstance(remember, list):
        return [_as_bool(remember[i]) if i < len(remember) else False for i in range(count)]
    flag = _as_bool(remember)
    return [flag] * count


class QuestionMemoryService:
    """Persist and match remembered question answers."""

    @staticmethod
    def _session_user_context(session: Any) -> dict[str, Any]:
        context = getattr(session, "user_context", None)
        return dict(context) if isinstance(context, dict) else {}

    @classmethod
    def _current_user_id(cls, session: Any) -> Optional[str]:
        context = cls._session_user_context(session)
        current_user_id = context.get("currentUserId")
        if current_user_id:
            return str(current_user_id)
        return None

    @classmethod
    def _user_scope(session: Any) -> str:
        return str(QuestionMemoryService._current_user_id(session) or "__shared__")

    @staticmethod
    def _storage_key(user_scope: str, context_key: str, fingerprint: str) -> str:
        return f"{_KEY_PREFIX}:{user_scope}:context:{context_key}:{fingerprint}"

    @staticmethod
    def _semantic_storage_key(user_scope: str, semantic_key: str) -> str:
        return f"{_KEY_PREFIX}:{user_scope}:semantic:{semantic_key}"

    @staticmethod
    def _semantic_prefix(user_scope: str) -> str:
        return f"{_KEY_PREFIX}:{user_scope}:semantic:"

    @staticmethod
    def _normalize_question(question: dict[str, Any]) -> dict[str, Any]:
        options = []
        for opt in question.get("options") or []:
            if isinstance(opt, dict):
                label = str(opt.get("label", "")).strip()
            else:
                label = str(opt).strip()
            if label:
                options.append(label)

        return {
            "question": str(question.get("question", "")).strip(),
            "header": str(question.get("header", "")).strip(),
            "type": str(question.get("type") or "choice").strip().lower(),
            "multiple": bool(question.get("multiple", False)),
            "options": options,
        }

    @classmethod
    def _question_text_for_matching(cls, question: dict[str, Any]) -> str:
        parts = [
            str(question.get("header", "")),
            str(question.get("question", "")),
            str(question.get("placeholder", "")),
        ]
        return " ".join(part.strip() for part in parts if part and part.strip())

    @staticmethod
    def _session_context_text(session: Any) -> str:
        parts = [
            str(getattr(session, "title", "") or ""),
            str(getattr(session, "category", "") or ""),
        ]
        metadata = getattr(session, "metadata", None)
        if isinstance(metadata, dict):
            for key in ("task", "taskTitle", "workflow", "description"):
                value = metadata.get(key)
                if isinstance(value, str):
                    parts.append(value)
        return " ".join(part for part in parts if part.strip())

    @classmethod
    def _question_summary(cls, session: Any, question: dict[str, Any]) -> str:
        normalized = cls._normalize_question(question)
        context_text = cls._session_context_text(session)
        question_text = cls._question_text_for_matching(question)
        summary = f"{context_text} Clarification answer: {normalized['header']} {question_text}"
        return re.sub(r"\s+", " ", summary).strip()

    @staticmethod
    def _semantic_tokens(text: str) -> set[str]:
        tokens = {tok.lower() for tok in _TOKEN_RE.findall(text or "") if tok.strip()}
        return {tok for tok in tokens if tok not in _GENERIC_SEMANTIC_TOKENS and len(tok) > 1}

    @classmethod
    def _question_tokens(cls, question: dict[str, Any]) -> set[str]:
        return cls._semantic_tokens(cls._question_text_for_matching(question))

    @classmethod
    def _context_tokens(cls, session: Any) -> set[str]:
        return cls._semantic_tokens(cls._session_context_text(session))

    @classmethod
    def _context_key(cls, session: Any) -> str:
        context_tokens = sorted(cls._context_tokens(session))
        if not context_tokens:
            return "__generic__"
        payload = json.dumps(context_tokens, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _token_overlap_score(current_tokens: set[str], stored_tokens: set[str]) -> float:
        if not current_tokens or not stored_tokens:
            return 0.0
        overlap = current_tokens & stored_tokens
        if not overlap:
            return 0.0
        return len(overlap) / max(len(current_tokens), len(stored_tokens))

    @classmethod
    def _semantic_match_score(
        cls,
        *,
        stored_summary: str,
        current_context_tokens: set[str],
        current_question: dict[str, Any],
        stored_record: dict[str, Any],
    ) -> float:
        current_question_tokens = cls._question_tokens(current_question)
        stored_question_tokens = set(stored_record.get("question_tokens") or [])
        if not stored_question_tokens:
            stored_question_tokens = cls._semantic_tokens(stored_summary)

        question_score = cls._token_overlap_score(current_question_tokens, stored_question_tokens)
        if question_score <= 0:
            return 0.0

        stored_context_tokens = set(stored_record.get("context_tokens") or [])
        context_score = cls._token_overlap_score(current_context_tokens, stored_context_tokens)
        if current_context_tokens or stored_context_tokens:
            if context_score < 0.25:
                return 0.0

        return min(1.0, (question_score * 0.7) + ((context_score or 1.0) * 0.3))

    @classmethod
    def _can_auto_recall(cls, question: dict[str, Any]) -> bool:
        normalized = cls._normalize_question(question)
        if normalized["type"] in _UNSAFE_TYPES:
            return False
        if _SENSITIVE_PROMPT_RE.search(cls._question_text_for_matching(question)):
            return False
        return True

    @classmethod
    def fingerprint(cls, question: dict[str, Any]) -> str:
        payload = json.dumps(
            cls._normalize_question(question),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @classmethod
    def _is_safe_to_remember(cls, question: dict[str, Any], answers: list[str]) -> bool:
        normalized = cls._normalize_question(question)
        if not cls._can_auto_recall(question):
            return False
        if not normalized["question"] or not answers:
            return False
        return cls._answers_fit_question(question, answers)

    @classmethod
    def _answers_fit_question(cls, question: dict[str, Any], answers: Iterable[str]) -> bool:
        normalized = cls._normalize_question(question)
        answer_list = [str(answer) for answer in answers if str(answer).strip()]
        if not answer_list:
            return False

        allowed = set(normalized["options"])
        if allowed:
            return all(answer in allowed for answer in answer_list)
        return True

    @staticmethod
    def _format_memory_entry(
        question: dict[str, Any],
        answers: list[str],
        question_summary: str,
    ) -> str:
        now = datetime.now(UTC).isoformat()
        question_text = str(question.get("question", "")).strip()
        answer_text = ", ".join(str(answer) for answer in answers)
        return (
            f"## Remembered Question Answer - {now}\n\n"
            f"- Question summary: {question_summary}\n"
            f"- Question: {question_text}\n"
            f"- Answer: {answer_text}\n"
            f"- Source: question_tool_remember\n"
        )

    @classmethod
    async def save_remembered_answers(
        cls,
        *,
        session_id: str,
        question_request: dict[str, Any],
        answers: list[list[str]],
        remember_flags: list[bool],
    ) -> int:
        """Save remembered answers for the request's currentUserId scope."""
        from flocks.session import Session

        session = await Session.get_by_id(session_id)
        if not session:
            log.warn("question_memory.save.no_session", {"session_id": session_id})
            return 0

        user_scope = cls._user_scope(session)
        context_key = cls._context_key(session)
        context_text = cls._session_context_text(session)
        context_tokens = sorted(cls._context_tokens(session))
        questions = question_request.get("questions") or []
        saved = 0
        now = datetime.now(UTC).isoformat()

        for idx, question in enumerate(questions):
            if idx >= len(answers) or idx >= len(remember_flags) or not remember_flags[idx]:
                continue
            answer = [str(item) for item in (answers[idx] or [])]
            if not isinstance(question, dict) or not cls._is_safe_to_remember(question, answer):
                continue

            fingerprint = cls.fingerprint(question)
            exact_key = cls._storage_key(user_scope, context_key, fingerprint)
            question_summary = cls._question_summary(session, question)
            semantic_key = f"{context_key}:{fingerprint}"
            storage_keys = [exact_key, cls._semantic_storage_key(user_scope, semantic_key)]

            existing = await Storage.get(exact_key) or {}
            record = {
                "current_user_id": user_scope,
                "context_key": context_key,
                "context_text": context_text,
                "context_tokens": context_tokens,
                "session_id": session_id,
                "fingerprint": fingerprint,
                "semantic_key": semantic_key,
                "question_summary": question_summary,
                "summary_tokens": sorted(cls._semantic_tokens(question_summary)),
                "question_tokens": sorted(cls._question_tokens(question)),
                "question": question,
                "normalized": cls._normalize_question(question),
                "answers": answer,
                "created_at": existing.get("created_at") or now,
                "updated_at": now,
                "hit_count": int(existing.get("hit_count") or 0),
            }
            for key in storage_keys:
                await Storage.set(key, record, "question_memory")
            saved += 1

            if getattr(session, "memory_enabled", False):
                await cls._append_to_account_memory(session, question, answer, question_summary)

        if saved:
            log.info("question_memory.saved", {"session_id": session_id, "count": saved})
        return saved

    @classmethod
    async def _append_to_account_memory(
        cls,
        session: Any,
        question: dict[str, Any],
        answers: list[str],
        question_summary: str,
    ) -> None:
        """Best-effort human-readable memory write."""
        try:
            from flocks.session.features.memory import SessionMemory

            memory = SessionMemory(
                session_id=session.id,
                project_id=session.project_id,
                workspace_dir=session.directory,
                enabled=session.memory_enabled,
                current_user_id=cls._current_user_id(session),
            )
            if await memory.initialize():
                await memory.write(
                    content=cls._format_memory_entry(question, answers, question_summary),
                    path=_MEMORY_PATH,
                    append=True,
                )
        except Exception as e:
            log.warn("question_memory.memory_append_failed", {
                "session_id": getattr(session, "id", None),
                "error": str(e),
            })

    @classmethod
    async def match_batch(
        cls,
        *,
        session_id: str,
        questions: list[dict[str, Any]],
    ) -> Optional[list[list[str]]]:
        """Return remembered answers when every question has a valid exact match."""
        matches = await cls.match_questions(session_id=session_id, questions=questions)
        if matches is None or any(match is None for match in matches):
            return None
        return cast(list[list[str]], matches)

    @classmethod
    async def match_questions(
        cls,
        *,
        session_id: str,
        questions: list[dict[str, Any]],
    ) -> Optional[list[Optional[list[str]]]]:
        """Return remembered answers per question; missing entries are None."""
        from flocks.session import Session

        if not questions:
            return []

        session = await Session.get_by_id(session_id)
        if not session:
            return None

        user_scope = cls._user_scope(session)
        context_key = cls._context_key(session)
        current_context_tokens = cls._context_tokens(session)
        answers: list[Optional[list[str]]] = []
        keys_and_records: list[tuple[str, dict[str, Any]]] = []

        for question in questions:
            if not isinstance(question, dict):
                answers.append(None)
                continue
            if not cls._can_auto_recall(question):
                answers.append(None)
                continue
            fingerprint = cls.fingerprint(question)
            key = cls._storage_key(user_scope, context_key, fingerprint)
            record = await Storage.get(key)
            exact_match = isinstance(record, dict)
            if not isinstance(record, dict):
                semantic_match = await cls._find_semantic_match(
                    user_scope=user_scope,
                    session=session,
                    current_context_tokens=current_context_tokens,
                    question=question,
                )
                if semantic_match:
                    key, record = semantic_match
            if not isinstance(record, dict):
                answers.append(None)
                continue
            if not exact_match:
                semantic_score = cls._semantic_match_score(
                    stored_summary=str(record.get("question_summary") or ""),
                    current_context_tokens=current_context_tokens,
                    current_question=question,
                    stored_record=record,
                )
                if semantic_score < 0.25:
                    answers.append(None)
                    continue
            record_answers = [str(item) for item in (record.get("answers") or [])]
            if not cls._answers_fit_question(question, record_answers):
                answers.append(None)
                continue
            answers.append(record_answers)
            keys_and_records.append((key, record))

        now = datetime.now(UTC).isoformat()
        for key, record in keys_and_records:
            record["hit_count"] = int(record.get("hit_count") or 0) + 1
            record["last_used_at"] = now
            await Storage.set(key, record, "question_memory")

        log.info("question_memory.matched", {
            "session_id": session_id,
            "count": sum(1 for answer in answers if answer is not None),
            "total": len(answers),
        })
        return answers

    @classmethod
    async def _find_semantic_match(
        cls,
        *,
        user_scope: str,
        session: Any,
        current_context_tokens: set[str],
        question: dict[str, Any],
    ) -> Optional[tuple[str, dict[str, Any]]]:
        """Find a remembered answer for a semantically similar question."""
        if not cls._can_auto_recall(question):
            return None

        best: Optional[tuple[str, dict[str, Any], float]] = None
        entries = await Storage.list_entries(prefix=cls._semantic_prefix(user_scope))
        for key, record in entries:
            if not isinstance(record, dict):
                continue
            score = cls._semantic_match_score(
                stored_summary=str(record.get("question_summary") or ""),
                current_context_tokens=current_context_tokens,
                current_question=question,
                stored_record=record,
            )
            if score < 0.25:
                continue
            if best is None or score > best[2]:
                best = (key, record, score)

        if best is None:
            return None
        return best[0], best[1]
