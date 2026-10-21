"""Unlimited-OCR API contract, image normalization, PDF rasterization and consent."""
import base64
from contextlib import contextmanager
import io
import json
import re
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import main as app_main, worker, unlimited_ocr
from app.main import app, recent_requests, region_requests, review_requests


@pytest.fixture
def client(monkeypatch):
    for queue in (recent_requests, region_requests, review_requests, app_main.tool_requests):
        queue.clear()
    monkeypatch.delenv("SUMOPOD_API_KEY", raising=False)
    monkeypatch.delenv("UNLIMITED_OCR_API_KEY", raising=False)
    monkeypatch.setenv("UNLIMITED_OCR_BASE_URL", "https://gpu.example.test/v1")
    monkeypatch.setenv("UNLIMITED_OCR_BACKEND", "vllm")
    monkeypatch.delenv("UNLIMITED_OCR_MODEL", raising=False)
    return TestClient(app)


def upload(client, name, data, path="/api/convert", consent=True):
    return client.post(path, content=data, headers={"X-Filename": name, **({"X-External-AI": "true"} if consent else {})})


def fake_provider(monkeypatch, text="<|det|>text [99, 199, 599, 299]<|/det|>TEKS GAMBAR", status=200, finish="stop", model=unlimited_ocr.MODEL):
    calls = []
    @contextmanager
    def stream(method, url, **kwargs):
        calls.append((url, kwargs))
        yield httpx.Response(status, json={"model": model, "choices": [{"message": {"content": text}, "finish_reason": finish}]})
    monkeypatch.setattr(unlimited_ocr.httpx, "stream", stream)
    return calls


def image_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (320, 200), "white").save(buffer, format="PNG")
    return buffer.getvalue()

def pdf_bytes(pages=2):
    images = [Image.new("RGB", (320, 200), "white") for _ in range(pages)]
    buffer = io.BytesIO()
    images[0].save(buffer, format="PDF", save_all=True, append_images=images[1:])
    return buffer.getvalue()

def text_layer_pdf_bytes():
    """A one-page PDF with selectable text and no external test dependency."""
    stream = b"BT /F1 12 Tf 72 720 Td (LAPISAN TERBACA) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf.extend(str(number).encode() + b" 0 obj\n" + obj + b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode())
    pdf.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(pdf)

def test_all_page_assets_are_available(client):
    assets = re.findall(r'(?:src|href)="(/static/[^\"]+)"', client.get("/").text)
    assert "/static/session.js" in assets
    for path in assets:
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.content, path
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get("/static/private.txt").status_code == 404

def test_text_and_office_conversion(client):
    text = upload(client, "catatan.txt", b"Judul\nisi\n")
    assert text.status_code == 200 and "Judul" in text.text
    assert text.headers["content-disposition"] == 'attachment; filename="catatan.md"'
    from docx import Document
    doc = Document()
    doc.add_paragraph("Isi penelitian")
    buffer = io.BytesIO()
    doc.save(buffer)
    response = upload(client, "laporan.docx", buffer.getvalue())
    assert response.status_code == 200 and "Isi penelitian" in response.text


def test_configuration_is_explicit_and_never_reuses_sumopod(client, monkeypatch):
    monkeypatch.delenv("UNLIMITED_OCR_BASE_URL")
    monkeypatch.setenv("SUMOPOD_API_KEY", "not-an-ocr-key")
    capabilities = client.get("/api/capabilities").json()
    assert capabilities == {"image_ocr": "unavailable", "pdf_ocr": "unavailable",
                            "image_ocr_model": unlimited_ocr.MODEL, "requires_external_consent": True, "ocr_provider": "unavailable"}
    assert upload(client, "a.png", image_bytes()).status_code == 503
    monkeypatch.setenv("UNLIMITED_OCR_BASE_URL", "https://gpu.example.test/v1")
    monkeypatch.setenv("UNLIMITED_OCR_API_KEY", "private-key")
    capabilities = client.get("/api/capabilities").json()
    assert capabilities["image_ocr"] == capabilities["pdf_ocr"] == "unlimited-ocr"
    assert "private-key" not in json.dumps(capabilities) and "example.test" not in json.dumps(capabilities)
    assert client.get("/health").json() == {"status": "ok"}
    assert "Unlimited-OCR" in client.get("/").text


@pytest.mark.parametrize("url", ["", "http://public.example/v1", "https://user:pass@example.com/v1",
                                  "https://example.com/v1?secret=x", "https://example.com:bad/v1", "http://[bad"])
def test_unsafe_or_malformed_configuration(url, monkeypatch):
    monkeypatch.setenv("UNLIMITED_OCR_BACKEND", "vllm")
    monkeypatch.setenv("UNLIMITED_OCR_BASE_URL", url)
    with pytest.raises(app_main.HTTPException) as error:
        unlimited_ocr.configuration()
    assert error.value.status_code == 503


def test_official_vllm_request_recipe_and_model_identity(client, monkeypatch):
    monkeypatch.setenv("UNLIMITED_OCR_API_KEY", "private-key")
    calls = fake_provider(monkeypatch)
    response = upload(client, "gambar.png", image_bytes())
    assert response.status_code == 200 and response.text == "TEKS GAMBAR"
    assert response.headers["x-ocr-engine"] == unlimited_ocr.MODEL
    url, kwargs = calls[0]
    assert url == "https://gpu.example.test/v1/chat/completions"
    assert kwargs["headers"] == {"Authorization": "Bearer private-key"}
    payload = kwargs["json"]
    assert payload["model"] == unlimited_ocr.MODEL
    assert payload["messages"][0]["content"][0]["text"] == "<image>document parsing."
    assert payload["skip_special_tokens"] is False
    assert payload["vllm_xargs"] == {"ngram_size": 35, "window_size": 128}
    assert payload["temperature"] == 0 and payload["stream"] is False
    assert kwargs["follow_redirects"] is False
    assert "thinking" not in payload
    assert len(calls) == 1  # No expensive generic-LLM vision probe or text repair.
    assert response.headers["cache-control"] == "no-store"
    fake_provider(monkeypatch, model="another-model")
    assert upload(client, "gambar.png", image_bytes()).status_code == 502


@pytest.mark.parametrize("fmt,extension", [("PNG", "png"), ("JPEG", "jpg"), ("WEBP", "webp"), ("TIFF", "tiff")])
def test_image_formats_normalize_strip_metadata_before_inference(client, monkeypatch, fmt, extension):
    image = Image.new("RGB", (600, 300), "white")
    exif = Image.Exif(); exif[315] = "PRIVATE AUTHOR"
    data = io.BytesIO(); image.save(data, fmt, exif=exif)
    calls = fake_provider(monkeypatch)
    assert upload(client, "scan." + extension, data.getvalue()).status_code == 200
    encoded = calls[0][1]["json"]["messages"][0]["content"][1]["image_url"]["url"]
    assert encoded.startswith("data:image/png;base64,")
    with Image.open(io.BytesIO(base64.b64decode(encoded.split(",")[1]))) as result:
        assert result.format == "PNG" and result.size == (600, 300)
        assert not result.getexif()


def test_consent_for_images_pdf_and_regions_blocks_network(client, monkeypatch):
    calls = fake_provider(monkeypatch)
    for name, data, path in [("a.png", image_bytes(), "/api/convert"),
                             ("a.png", image_bytes(), "/api/regions"),
                             ("a.pdf", pdf_bytes(), "/api/convert"),
                             ("a.pdf", pdf_bytes(), "/api/pdf-preview?page=1")]:
        assert upload(client, name, data, path, consent=False).status_code == 400
    assert not calls
    preview = upload(client, "a.pdf", pdf_bytes(), "/api/pdf-preview?page=1&preview_only=true", consent=False)
    assert preview.status_code == 200 and preview.json()["preview_only"] is True
    assert not preview.json()["page_text"] and not calls
    assert client.post("/api/pdf-language-check", json={"text": "PRIVATE TEXT"}).status_code == 400


def test_grounding_formats_reading_order_and_table_preservation():
    raw = ("<|ref|>title<|/ref|><|det|>[[0, 0, 999, 99]]<|/det|># Judul\n"
           "<|det|>text [0, 200, 999, 299]<|/det|>Paragraf awal\n"
           "<|ref|>image<|/ref|><|det|>[[1, 1, 2, 2]]<|/det|>image placeholder\n"
           "<|det|>table [0, 400, 999, 999]<|/det|>| A | B |\n|---|---|\n| 1 | 2 |")
    result = unlimited_ocr.parse_output(raw)
    assert result["markdown"] == "# Judul\n\nParagraf awal\n\n| A | B |\n|---|---|\n| 1 | 2 |"
    assert [r["text"] for r in result["regions"]] == ["# Judul", "Paragraf awal", "| A | B |\n|---|---|\n| 1 | 2 |"]
    assert result["regions"][0]["w"] == 1 and result["regions"][-1]["h"] == 599 / 999
    # Coordinates do not reorder the model's multi-column reading sequence.
    raw = "<|det|>text [500, 0, 999, 99]<|/det|>First\n<|det|>text [0, 0, 400, 99]<|/det|>Second"
    assert unlimited_ocr.parse_output(raw)["markdown"] == "First\n\nSecond"


@pytest.mark.parametrize("bbox", ["[-1,0,20,20]", "[0,0,1000,20]", "[10,0,2,20]", "[true,0,20,20]", "[NaN,0,20,20]", "[1,2]", "not-json"])
def test_invalid_grounding_keeps_transcription_without_fabricating_boxes(bbox):
    parsed = unlimited_ocr.parse_output(f"<|det|>text {bbox}<|/det|>TEKS AMAN")
    assert parsed == {"markdown": "TEKS AMAN", "regions": []}
    assert unlimited_ocr.parse_output("<table><tr><td>7</td></tr></table>")["markdown"].startswith("<table>")


def test_regions_use_model_coordinates_not_second_model(client, monkeypatch):
    calls = fake_provider(monkeypatch)
    response = upload(client, "a.png", image_bytes(), "/api/regions")
    assert response.status_code == 200
    regions = response.json()["regions"]
    assert regions[0]["source"] == "unlimited-ocr" and regions[0]["x"] == 99 / 999
    assert len(calls) == 1


def test_pdf_rasterizes_each_page_not_original_pdf_or_text_layer(client, monkeypatch):
    calls = fake_provider(monkeypatch, text="TEKS DARI MODEL")
    pdf = text_layer_pdf_bytes()
    assert upload(client, "a.pdf", pdf).status_code == 200
    result = upload(client, "a.pdf", pdf, "/api/pdf-preview?page=1")
    assert result.status_code == 200
    payload = result.json()
    assert payload["page_source"] == "unlimited-ocr" and payload["page_text"] == "TEKS DARI MODEL"
    assert "LAPISAN TERBACA" not in payload["page_text"]
    image = calls[0][1]["json"]["messages"][0]["content"][1]["image_url"]["url"]
    assert image == payload["image"] and image.startswith("data:image/jpeg;base64,")
    assert base64.b64decode(image.split(",")[1]).startswith(b"\xff\xd8\xff")
    with Image.open(io.BytesIO(base64.b64decode(image.split(",")[1]))) as decoded:
        assert max(decoded.size) == 2800
    zoom = upload(client, "a.pdf", pdf, "/api/pdf-preview?page=1&zoom=true").json()
    with Image.open(io.BytesIO(base64.b64decode(zoom["image"].split(",")[1]))) as decoded:
        assert max(decoded.size) == 3200
    assert upload(client, "a.pdf", pdf, "/api/pdf-preview?page=2").status_code == 400
    assert len(calls) == 2


@pytest.mark.parametrize("status,expected", [(401,503), (403,503), (429,429), (500,502), (302,502)])
def test_provider_failures_never_become_success_and_preserve_pdf_preview(client, monkeypatch, status, expected):
    fake_provider(monkeypatch, status=status)
    response = upload(client, "a.png", image_bytes())
    assert response.status_code == expected
    result = upload(client, "a.pdf", pdf_bytes(), "/api/pdf-preview?page=1").json()
    assert result["ocr_status"] == expected and result["page_text"] == "" and result["page_source"] is None
    assert result["image"].startswith("data:image/jpeg;base64,")


@pytest.mark.parametrize("text,finish,expected", [("PARTIAL", "length",502), ("", "stop",422), ("<|det|>image [0,0,10,10]<|/det|>","stop",422)])
def test_truncated_or_empty_ocr_not_downloadable(client, monkeypatch, text, finish, expected):
    fake_provider(monkeypatch, text=text, finish=finish)
    assert upload(client, "a.png", image_bytes()).status_code == expected
    result = upload(client, "a.pdf", pdf_bytes(), "/api/pdf-preview?page=1").json()
    assert result["ocr_status"] == expected and not result["page_text"]


def test_timeout_and_malformed_provider_reply(client, monkeypatch):
    def timeout(*a, **kw):
        raise httpx.ReadTimeout("private provider debug message")
    monkeypatch.setattr(unlimited_ocr.httpx, "stream", timeout)
    response = upload(client, "a.png", image_bytes())
    assert response.status_code == 504 and "private provider" not in response.text
    @contextmanager
    def bad(*a, **kw):
        yield httpx.Response(200, content=b"not json")
    monkeypatch.setattr(unlimited_ocr.httpx, "stream", bad)
    assert upload(client, "a.png", image_bytes()).status_code == 502


def test_validates_uploads_and_pdf_limit(client, monkeypatch):
    calls = fake_provider(monkeypatch)
    assert upload(client, "../../secret.txt", b"x").status_code == 400
    assert upload(client, "malware.exe", b"x").status_code == 415
    assert upload(client, "empty.txt", b"").status_code == 400
    assert upload(client, "wrong.pdf", b"not a pdf").status_code == 415
    assert upload(client, "wrong.png", b"not png").status_code == 415
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as file:
        file.writestr("oops", "not a docx")
    assert upload(client, "wrong.docx", archive.getvalue()).status_code == 415
    pdf30, pdf31 = pdf_bytes(30), pdf_bytes(31)
    assert worker.pdf_pages(pdf30) == 30
    assert worker.read_pdf_preview(pdf30, 1)[0] == 0
    assert upload(client, "max.pdf", pdf30).status_code == 200
    assert worker.read_pdf_text(pdf31)[0] == 8
    for path in ("/api/convert", "/api/pdf-preview?page=1"):
        result = upload(client, "too-many.pdf", pdf31, path)
        assert result.status_code == 413 and result.json()["detail"] == "Dilewati: PDF melebihi 30 halaman"
    assert upload(client, "next.txt", b"berikutnya").status_code == 200
    assert not calls


def test_optional_language_review_is_text_only_and_independent(client, monkeypatch):
    monkeypatch.setenv("SUMOPOD_API_KEY", "test-key")
    seen = []
    def review(url, **kwargs):
        seen.append(kwargs["json"])
        content = json.dumps({"languages": ["Indonesia"], "assessment": "perlu_tinjau",
                              "suspect_spans": ["teks janggal", "kutipan rekaan"]})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]})
    monkeypatch.setattr(app_main.httpx, "post", review)
    text = "Ini adalah teks janggal pada hasil OCR halaman."
    response = client.post("/api/pdf-language-check", json={"text": text}, headers={"X-External-AI": "true"})
    assert response.status_code == 200 and response.json()["suspect_spans"] == ["teks janggal"]
    assert response.json()["scope"] == "text_only" and seen[0]["model"] == "deepseek-v4.1-flash:netra"
    assert text in seen[0]["messages"][0]["content"] and "image_url" not in json.dumps(seen[0])
    monkeypatch.setattr(app_main.httpx, "post", lambda *a, **kw: httpx.Response(503))
    assert client.post("/api/pdf-language-check", json={"text": text}, headers={"X-External-AI": "true"}).status_code == 502
    fake_provider(monkeypatch, text="TEKS TETAP TERSEDIA")
    assert upload(client, "a.pdf", pdf_bytes(), "/api/pdf-preview?page=1").json()["page_text"] == "TEKS TETAP TERSEDIA"


@pytest.mark.parametrize("reply", [{"choices": [None]}, {"choices": [[]]}, {"choices": "bad"},
                                    {"choices": []}, {"model": unlimited_ocr.MODEL, "choices": [
                                        {"message": {"content": chr(0xd800)}, "finish_reason": "stop"}]}])
def test_malformed_choice_or_unicode_is_a_provider_error(client, monkeypatch, reply):
    @contextmanager
    def stream(*args, **kwargs):
        yield httpx.Response(200, content=json.dumps(reply).encode())
    monkeypatch.setattr(unlimited_ocr.httpx, "stream", stream)
    assert upload(client, "a.png", image_bytes()).status_code == 502


def test_oversized_provider_body_is_bounded(client, monkeypatch):
    @contextmanager
    def stream(*args, **kwargs):
        yield httpx.Response(200, content=b"x" * (unlimited_ocr.MAX_OUTPUT_BYTES + 65537))
    monkeypatch.setattr(unlimited_ocr.httpx, "stream", stream)
    response = upload(client, "a.png", image_bytes())
    assert response.status_code == 502 and "terlalu besar" in response.json()["detail"]
