from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
async def isolated_memory_home(tmp_path, monkeypatch):
    from flocks.config.config import Config
    from flocks.memory.manager import MemoryManager
    from flocks.session import Session
    from flocks.storage.storage import Storage

    data_dir = tmp_path / "data"
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))
    Config._global_config = None
    Config.clear_cache()
    Storage._initialized = False
    Storage._db_path = None
    MemoryManager._instances.clear()
    Session.invalidate_cache()

    await Storage.init(data_dir / "flocks.db")

    yield data_dir

    MemoryManager._instances.clear()
    Config._global_config = None
    Config.clear_cache()
    Storage._initialized = False
    Storage._db_path = None
    Session.invalidate_cache()


@pytest.mark.asyncio
async def test_memory_write_is_scoped_by_current_user_id(isolated_memory_home):
    from flocks.memory import MemoryConfig, MemoryManager

    manager = MemoryManager.get_instance(
        project_id="proj",
        workspace_dir=".",
        config=MemoryConfig(enabled=True),
        current_user_id="user_a",
    )

    path = await manager.write_memory("alice note", path="daily.md", append=False)

    assert path == "users/user_a/daily.md"
    assert (isolated_memory_home / "memory" / "users" / "user_a" / "daily.md").read_text() == "alice note"

    with pytest.raises(ValueError):
        await manager.write_memory("bad", path="users/user_b/daily.md", append=False)


@pytest.mark.asyncio
async def test_memory_search_filters_by_current_user_id(isolated_memory_home):
    from flocks.storage import Storage, insert_chunks, vector_search, fts_search

    chunks = [
        {
            "id": "chunk-a",
            "path": "users/user_a/a.md",
            "project_id": "proj",
            "current_user_id": "user_a",
            "session_id": "session-a",
            "source": "memory",
            "start_line": 1,
            "end_line": 1,
            "hash": "hash-a",
            "text": "alpha private note",
            "embedding": [1.0, 0.0],
            "embedding_model": "test",
            "embedding_dims": 2,
        },
        {
            "id": "chunk-b",
            "path": "users/user_b/b.md",
            "project_id": "proj",
            "current_user_id": "user_b",
            "session_id": "session-b",
            "source": "memory",
            "start_line": 1,
            "end_line": 1,
            "hash": "hash-b",
            "text": "alpha other note",
            "embedding": [1.0, 0.0],
            "embedding_model": "test",
            "embedding_dims": 2,
        },
    ]

    await insert_chunks(Storage.get_db_path(), chunks)

    vector_results = await vector_search(
        Storage.get_db_path(),
        project_id="proj",
        embedding=[1.0, 0.0],
        current_user_id="user_a",
    )
    fts_results = await fts_search(
        Storage.get_db_path(),
        project_id="proj",
        query="alpha",
        current_user_id="user_a",
    )

    assert [r["id"] for r in vector_results] == ["chunk-a"]
    assert [r["id"] for r in fts_results] == ["chunk-a"]


@pytest.mark.asyncio
async def test_memory_bootstrap_reads_only_current_user_id(isolated_memory_home):
    from flocks.memory.bootstrap import MemoryBootstrap

    global_memory = isolated_memory_home / "memory" / "MEMORY.md"
    global_memory.parent.mkdir(parents=True, exist_ok=True)
    global_memory.write_text("global note should be ignored", encoding="utf-8")

    user_memory = isolated_memory_home / "memory" / "users" / "user_a" / "MEMORY.md"
    user_memory.parent.mkdir(parents=True, exist_ok=True)
    user_memory.write_text("alice private note", encoding="utf-8")

    bootstrap = MemoryBootstrap(current_user_id="user_a")
    result = await bootstrap.load_main_memory()

    assert result is not None
    assert result["content"] == "alice private note"
    assert "users\\user_a" in result["abs_path"] or "users/user_a" in result["abs_path"]


@pytest.mark.asyncio
async def test_session_create_populates_and_inherits_current_user_id(isolated_memory_home):
    from flocks.session import Session

    parent = await Session.create(
        project_id="proj_session_context",
        directory=".",
        title="parent",
        user_context={"currentUserId": "usr_parent", "knowledgeBaseIds": ["kb_a"]},
        memory_enabled=False,
    )
    child = await Session.create(
        project_id="proj_session_context",
        directory=".",
        title="child",
        parent_id=parent.id,
        memory_enabled=False,
    )

    assert parent.user_context["currentUserId"] == "usr_parent"
    assert parent.user_context["knowledgeBaseIds"] == ["kb_a"]
    assert child.user_context == parent.user_context


@pytest.mark.asyncio
async def test_question_memory_does_not_cross_unrelated_tasks(isolated_memory_home):
    from flocks.memory.question_memory import QuestionMemoryService
    from flocks.session import Session

    first = await Session.create(
        project_id="proj_question_scope",
        directory=".",
        title="skills synchronization task",
        user_context={"currentUserId": "user_question_scope"},
        memory_enabled=False,
    )
    second = await Session.create(
        project_id="proj_question_scope",
        directory=".",
        title="weather alert task",
        user_context={"currentUserId": "user_question_scope"},
        memory_enabled=False,
    )

    skills_service_question = {
        "question": "Enter the external service URL",
        "header": "Integration configuration",
        "type": "text",
        "options": [],
    }
    await QuestionMemoryService.save_remembered_answers(
        session_id=first.id,
        question_request={"questions": [skills_service_question]},
        answers=[["https://skills.example.test/api"]],
        remember_flags=[True],
    )

    weather_service_question = {
        "question": "Enter the external service URL",
        "header": "Integration configuration",
        "type": "text",
        "options": [],
    }
    partial = await QuestionMemoryService.match_questions(
        session_id=second.id,
        questions=[weather_service_question],
    )

    assert partial == [None]
