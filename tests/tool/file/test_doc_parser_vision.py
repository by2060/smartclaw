import base64
import io
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter

from smartclaw.tool.registry import ToolContext, ToolRegistry


def _load_module():
    import smartclaw.tool.file.doc_parser as module

    return module


@pytest.fixture
def doc_parser_module():
    module = _load_module()
    yield module
    ToolRegistry._tools.pop("doc_parser", None)


def _png_bytes(label: str) -> bytes:
    image = Image.new("RGB", (800, 600), color="white")
    drawer = ImageDraw.Draw(image)
    drawer.text((40, 40), label, fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _create_text_pdf(path: Path, text: str) -> None:
    import fitz

    document = fitz.open()
    try:
        page = document.new_page()
        page.insert_text((72, 72), text)
        document.save(path)
    finally:
        document.close()


def _create_image_pdf(path: Path, labels: list[str]) -> None:
    import fitz

    document = fitz.open()
    try:
        for label in labels:
            page = document.new_page()
            page.insert_image(page.rect, stream=_png_bytes(label))
        document.save(path)
    finally:
        document.close()


def _create_mixed_pdf(path: Path) -> None:
    import fitz

    document = fitz.open()
    try:
        text_page = document.new_page()
        text_page.insert_text((72, 72), "This is a text page. " * 20)
        image_page = document.new_page()
        image_page.insert_image(image_page.rect, stream=_png_bytes("第二页扫描内容"))
        document.save(path)
    finally:
        document.close()


def _create_large_mixed_pdf(path: Path, *, scan_pages: int, trailing_text: str) -> None:
    import fitz

    document = fitz.open()
    try:
        for index in range(scan_pages):
            page = document.new_page()
            page.insert_image(page.rect, stream=_png_bytes(f"扫描页 {index + 1}"))
        text_page = document.new_page()
        text_page.insert_text((72, 72), trailing_text)
        document.save(path)
    finally:
        document.close()


def _create_encrypted_pdf(path: Path) -> None:
    plain = path.with_name("plain.pdf")
    _create_text_pdf(plain, "这是需要加密的 PDF。" * 10)
    reader = PdfReader(str(plain))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt("secret")
    with path.open("wb") as handle:
        writer.write(handle)


def test_run_extractors_detailed_skips_vision_for_text_pdf(tmp_path, monkeypatch, doc_parser_module):
    source = tmp_path / "text.pdf"
    _create_text_pdf(source, "Plain PDF text content. " * 20)

    called: list[Path] = []

    def fake_vision(file_path: Path, *, session_id: str | None = None):
        called.append(file_path)
        return "unexpected", [], True

    monkeypatch.setattr(doc_parser_module, "_extract_pdf_with_vision", fake_vision)

    outcome = doc_parser_module._run_extractors_detailed(source, session_id="test")

    assert called == []
    assert outcome.parser_name in {"markitdown", "pymupdf", "pypdf"}
    assert "Plain PDF text content" in outcome.content


def test_extract_pdf_with_vision_parses_mixed_pdf(tmp_path, monkeypatch, doc_parser_module):
    source = tmp_path / "mixed.pdf"
    _create_mixed_pdf(source)

    calls: list[int] = []

    def fake_model_call(**kwargs):
        calls.append(kwargs["page_no"])
        return "识别到的扫描页文本"

    monkeypatch.setattr(doc_parser_module, "_call_pdf_vision_model", fake_model_call)

    content, errors, attempted = doc_parser_module._extract_pdf_with_vision(source, session_id="test")

    assert attempted is True
    assert errors == []
    assert calls == [2]
    assert "This is a text page" in content
    assert "## 第 2 页（图片识别）" in content
    assert "识别到的扫描页文本" in content


def test_run_extractors_detailed_uses_vision_for_image_pdf(tmp_path, monkeypatch, doc_parser_module):
    source = tmp_path / "image.pdf"
    _create_image_pdf(source, ["扫描第一页"])

    def fake_model_call(**kwargs):
        assert kwargs["page_no"] == 1
        return "扫描件识别结果"

    monkeypatch.setattr(doc_parser_module, "_call_pdf_vision_model", fake_model_call)

    outcome = doc_parser_module._run_extractors_detailed(source, session_id="vision-session")

    assert outcome.parser_name == "vision"
    assert outcome.failure_reason is None
    assert "## 第 1 页（图片识别）" in outcome.content
    assert "扫描件识别结果" in outcome.content


def test_run_extractors_detailed_merges_mixed_pdf(tmp_path, monkeypatch, doc_parser_module):
    source = tmp_path / "mixed-detailed.pdf"
    _create_mixed_pdf(source)

    monkeypatch.setattr(doc_parser_module, "_call_pdf_vision_model", lambda **kwargs: "扫描页补充内容")

    outcome = doc_parser_module._run_extractors_detailed(source, session_id="mixed")

    assert outcome.parser_name == "vision"
    assert "This is a text page" in outcome.content
    assert "## 第 2 页（图片识别）" in outcome.content
    assert "扫描页补充内容" in outcome.content


def test_call_pdf_vision_model_uses_multimodal_llm(monkeypatch, doc_parser_module):
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["init"] = kwargs

        def ask_messages(self, messages, **kwargs):
            captured["messages"] = messages
            captured["kwargs"] = kwargs
            return "模型返回"

    monkeypatch.setattr(doc_parser_module, "LLMClient", FakeClient)

    config = doc_parser_module._PdfVisionConfig(
        dpi=150,
        max_pages=50,
        max_tokens=321,
        timeout_s=12.5,
        model="demo/model",
        provider_id="demo",
    )
    out = doc_parser_module._call_pdf_vision_model(
        image_b64=base64.b64encode(b"png-bytes").decode("utf-8"),
        page_no=3,
        raw_text="已有零碎文本",
        session_id="session-1",
        config=config,
    )

    assert out == "模型返回"
    assert captured["init"] == {
        "model": "demo/model",
        "provider_id": "demo",
        "session_id": "session-1",
    }
    messages = captured["messages"]
    assert len(messages) == 1
    assert messages[0].content[0]["type"] == "text"
    assert "第 3 页" in messages[0].content[0]["text"]
    assert messages[0].content[1] == {
        "type": "image",
        "mimeType": "image/png",
        "data": base64.b64encode(b"png-bytes").decode("utf-8"),
    }
    assert captured["kwargs"]["max_tokens"] == 321
    assert captured["kwargs"]["timeout_s"] == 12.5


def test_load_pdf_vision_config_defaults(doc_parser_module):
    config = doc_parser_module._load_pdf_vision_config()

    assert config.max_pages == 50
    assert config.max_tokens == 3000


@pytest.mark.asyncio
async def test_doc_parser_reports_password_required(tmp_path, doc_parser_module):
    source = tmp_path / "encrypted.pdf"
    output = tmp_path / "encrypted.md"
    _create_encrypted_pdf(source)

    result = await doc_parser_module.doc_parser(
        ToolContext(session_id="test", message_id="test"),
        input_path=str(source),
        output_path=str(output),
    )

    assert result.success is False
    assert "PDF 需要密码" in (result.error or "")


@pytest.mark.asyncio
async def test_doc_parser_reports_text_extraction_failure(tmp_path, monkeypatch, doc_parser_module):
    source = tmp_path / "text-fail.pdf"
    output = tmp_path / "text-fail.md"
    _create_text_pdf(source, "这是一份本应可读的 PDF。" * 20)

    monkeypatch.setattr(doc_parser_module, "_extract_with_markitdown", lambda _: "")
    monkeypatch.setattr(doc_parser_module, "_extract_pdf_with_pymupdf", lambda _: "")
    monkeypatch.setattr(doc_parser_module, "_extract_pdf_with_pypdf", lambda _: "")
    monkeypatch.setattr(
        doc_parser_module,
        "_extract_pdf_with_vision",
        lambda _file_path, *, session_id=None: ("", ["vision: skipped"], False),
    )

    result = await doc_parser_module.doc_parser(
        ToolContext(session_id="test", message_id="test"),
        input_path=str(source),
        output_path=str(output),
    )

    assert result.success is False
    assert "PDF 文本提取失败" in (result.error or "")


@pytest.mark.asyncio
async def test_doc_parser_reports_vision_failure(tmp_path, monkeypatch, doc_parser_module):
    source = tmp_path / "vision-fail.pdf"
    output = tmp_path / "vision-fail.md"
    _create_image_pdf(source, ["扫描页"])

    monkeypatch.setattr(
        doc_parser_module,
        "_call_pdf_vision_model",
        lambda **kwargs: (_ for _ in ()).throw(TimeoutError("timed out")),
    )

    result = await doc_parser_module.doc_parser(
        ToolContext(session_id="test", message_id="test"),
        input_path=str(source),
        output_path=str(output),
    )

    assert result.success is False
    assert "PDF 图片页视觉识别失败" in (result.error or "")


@pytest.mark.asyncio
async def test_doc_parser_reports_unreadable_pdf(tmp_path, monkeypatch, doc_parser_module):
    source = tmp_path / "unreadable.pdf"
    output = tmp_path / "unreadable.md"
    source.write_bytes(b"not-a-real-pdf")

    monkeypatch.setattr(doc_parser_module, "_extract_with_markitdown", lambda _: "")

    result = await doc_parser_module.doc_parser(
        ToolContext(session_id="test", message_id="test"),
        input_path=str(source),
        output_path=str(output),
    )

    assert result.success is False
    assert "PDF 无法解析" in (result.error or "")


def test_run_extractors_detailed_handles_corrupt_pdf(tmp_path, doc_parser_module):
    source = tmp_path / "broken.pdf"
    source.write_bytes(b"not-a-real-pdf")

    outcome = doc_parser_module._run_extractors_detailed(source, session_id="broken")

    assert isinstance(outcome.content, str)
    assert isinstance(outcome.errors, list)


def test_extract_pdf_with_vision_handles_blank_pdf(tmp_path, monkeypatch, doc_parser_module):
    import fitz

    source = tmp_path / "blank.pdf"
    document = fitz.open()
    try:
        document.new_page()
        document.save(source)
    finally:
        document.close()

    monkeypatch.setattr(doc_parser_module, "_call_pdf_vision_model", lambda **kwargs: "")

    content, errors, attempted = doc_parser_module._extract_pdf_with_vision(source, session_id="blank")

    assert content == ""
    assert attempted is True
    assert errors == ["vision page 1: extracted empty content"]


def test_extract_pdf_with_vision_limits_pages_and_tokens(tmp_path, monkeypatch, doc_parser_module):
    source = tmp_path / "large-scan.pdf"
    _create_image_pdf(source, [f"扫描页 {index}" for index in range(1, 56)])

    monkeypatch.setenv("SMARTCLAW_DOC_PARSER_PDF_VISION_MAX_PAGES", "50")
    monkeypatch.setenv("SMARTCLAW_DOC_PARSER_PDF_VISION_MAX_TOKENS", "321")

    calls: list[tuple[int, int]] = []

    def fake_model_call(**kwargs):
        calls.append((kwargs["page_no"], kwargs["config"].max_tokens))
        return f"第 {kwargs['page_no']} 页"

    monkeypatch.setattr(doc_parser_module, "_call_pdf_vision_model", fake_model_call)

    content, errors, attempted = doc_parser_module._extract_pdf_with_vision(source, session_id="large")

    assert attempted is True
    assert errors == []
    assert len(calls) == 50
    assert calls[0] == (1, 321)
    assert calls[-1] == (50, 321)
    assert "已处理 50 个扫描/图片页；另有 5 个扫描/图片页因上限 50 未执行视觉识别" in content


def test_extract_pdf_with_vision_only_limits_scan_pages(tmp_path, monkeypatch, doc_parser_module):
    source = tmp_path / "large-mixed.pdf"
    trailing_text = "Trailing text page stays available. " * 10
    _create_large_mixed_pdf(source, scan_pages=55, trailing_text=trailing_text)

    monkeypatch.setenv("SMARTCLAW_DOC_PARSER_PDF_VISION_MAX_PAGES", "50")
    monkeypatch.setattr(doc_parser_module, "_call_pdf_vision_model", lambda **kwargs: f"第 {kwargs['page_no']} 页")

    content, errors, attempted = doc_parser_module._extract_pdf_with_vision(source, session_id="large-mixed")

    assert attempted is True
    assert errors == []
    assert "## 第 1 页（图片识别）" in content
    assert "## 第 50 页（图片识别）" in content
    assert "## 第 51 页（图片识别）" not in content
    assert "Trailing text page stays available" in content
    assert "已处理 50 个扫描/图片页；另有 5 个扫描/图片页因上限 50 未执行视觉识别" in content
