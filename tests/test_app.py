import base64
import io
import json
import os
import time
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont

from app.main import MAX_REQUESTS_PER_MINUTE, app, recent_requests, region_requests, remote_requests, run_worker
from app import worker


@pytest.fixture
def client(monkeypatch):
    recent_requests.clear()
    remote_requests.clear()
    region_requests.clear()
    monkeypatch.delenv("SUMOPOD_API_KEY", raising=False)
    return TestClient(app)


def upload(client, name, content):
    return client.post(
        "/api/convert",
        content=content,
        headers={"X-Filename": name},
    )


def test_health_and_home(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/api/capabilities").json() == {"image_ocr": "tesseract"}
    assert "MarkItDown Web" in client.get("/").text
    assert client.get("/static/zip.js").status_code == 200
    assert client.get("/static/app.js").headers["cache-control"] == "no-cache"


def test_retired_provider_keys_do_not_enable_remote_ocr(client, monkeypatch):
    monkeypatch.setenv("DEEPINFRA_API_KEY", "obsolete-key")
    monkeypatch.setenv("CIRRASCALE_API_KEY", "old-key")
    monkeypatch.setenv("HF_OLMOCR_ENDPOINT_URL", "https://test.us-east-1.aws.endpoints.huggingface.cloud")
    monkeypatch.setenv("HF_TOKEN", "old-token")
    assert client.get("/api/capabilities").json() == {"image_ocr": "tesseract"}


def test_real_text_conversion(client):
    response = upload(client, "catatan.txt", "Judul\nisi\n".encode())
    assert response.status_code == 200, response.text
    assert "Judul" in response.text
    assert response.headers["content-disposition"] == 'attachment; filename="catatan.md"'


def test_real_docx_conversion(client):
    from docx import Document

    doc = Document()
    doc.add_heading("Laporan uji", level=1)
    doc.add_paragraph("Isi penelitian")
    buffer = io.BytesIO()
    doc.save(buffer)
    response = upload(client, "laporan.docx", buffer.getvalue())
    assert response.status_code == 200, response.text
    assert "Laporan uji" in response.text
    assert "Isi penelitian" in response.text


def test_real_xlsx_conversion(client):
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.active.append(["Kelas", "Jumlah"])
    workbook.active.append(["Gamble", 42])
    buffer = io.BytesIO()
    workbook.save(buffer)
    response = upload(client, "data.xlsx", buffer.getvalue())
    assert response.status_code == 200, response.text
    assert "Gamble" in response.text
    assert "42" in response.text


def test_real_pptx_conversion(client):
    from pptx import Presentation

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "Judul slide"
    slide.placeholders[1].text = "Isi slide"
    buffer = io.BytesIO()
    presentation.save(buffer)
    response = upload(client, "presentasi.pptx", buffer.getvalue())
    assert response.status_code == 200, response.text
    assert "Judul slide" in response.text
    assert "Isi slide" in response.text


@pytest.mark.parametrize("format_name,extension", [
    ("PNG", ".png"), ("JPEG", ".jpg"), ("WEBP", ".webp"),
])
def test_real_image_ocr(client, format_name, extension):
    image = Image.new("RGB", (640, 150), "white")
    ImageDraw.Draw(image).text(
        (20, 25), "HELLO 123", font=ImageFont.load_default(size=72), fill="black",
    )
    buffer = io.BytesIO()
    image.save(buffer, format=format_name)
    response = upload(client, "screenshot" + extension, buffer.getvalue())
    assert response.status_code == 200, response.text
    assert "HELLO123" in response.text.replace(" ", "")
    assert response.headers["x-ocr-engine"] == "tesseract"


def test_transparent_screenshot_ocr(client):
    image = Image.new("RGBA", (640, 150), (255, 255, 255, 0))
    ImageDraw.Draw(image).text(
        (20, 25), "HELLO 123", font=ImageFont.load_default(size=72), fill=(0, 0, 0, 255),
    )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    response = upload(client, "transparent.png", buffer.getvalue())
    assert response.status_code == 200, response.text
    assert "HELLO123" in response.text.replace(" ", "")


def test_image_region_positions_and_local_provenance(client, monkeypatch):
    monkeypatch.setenv("SUMOPOD_API_KEY", "configured-but-not-used-here")
    image = Image.new("RGB", (900, 220), "white")
    ImageDraw.Draw(image).text((30, 50), "HELLO 123", font=ImageFont.load_default(size=72), fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    response = client.post("/api/regions", content=buffer.getvalue(), headers={"X-Filename": "screenshot.png"})
    assert response.status_code == 200, response.text
    assert response.json()["engine"] == "tesseract"
    regions = response.json()["regions"]
    assert regions and "HELLO" in " ".join(region["text"] for region in regions)
    assert all(0 <= region[key] <= 1 for region in regions for key in ("x", "y", "w", "h"))
    assert response.headers["cache-control"] == "no-store"
    assert client.post("/api/regions", content=b"not an image", headers={"X-Filename": "file.txt"}).status_code == 415


def scanned_pdf(page_count=4):
    pages = []
    for number in range(1, page_count + 1):
        image = Image.new("RGB", (900, 500), "white")
        ImageDraw.Draw(image).text(
            (55, 75), f"HALAMAN {number} TOTAL {number}25000",
            font=ImageFont.load_default(size=65), fill="black",
        )
        pages.append(image)
    buffer = io.BytesIO()
    pages[0].save(buffer, format="PDF", save_all=True, append_images=pages[1:])
    return buffer.getvalue()


def test_scanned_pdf_ocr_and_visible_boxes_on_each_page(client):
    scanned = scanned_pdf()
    response = upload(client, "faktur.pdf", scanned)
    assert response.status_code == 200, response.text
    assert response.headers["x-ocr-engine"] == "tesseract-pdf"
    assert all(f"Halaman {page}" in response.text for page in range(1, 5))
    assert "125000" in response.text.replace(" ", "")
    assert "425000" in response.text.replace(" ", "")
    for page in range(1, 5):
        preview = client.post(
            f"/api/pdf-preview?page={page}", content=scanned,
            headers={"X-Filename": "faktur.pdf"},
        )
        assert preview.status_code == 200, preview.text[:100]
        result = preview.json()
        assert (result["page"], result["total_pages"]) == (page, 4)
        assert result["image"].startswith("data:image/jpeg;base64,")
        with Image.open(io.BytesIO(base64.b64decode(result["image"].split(",", 1)[1]))) as rendered:
            assert rendered.format == "JPEG"
        assert result["regions"]
        assert str(page) in " ".join(region["text"] for region in result["regions"])
        assert all(0 <= region[key] <= 1 for region in result["regions"] for key in ("x", "y", "w", "h"))
        assert preview.headers["cache-control"] == "no-store"
    assert client.post("/api/pdf-preview?page=5", content=scanned, headers={"X-Filename": "faktur.pdf"}).status_code == 400
    assert client.post("/api/pdf-preview", content=scanned, headers={"X-Filename": "faktur.txt"}).status_code == 415


def test_pdf_preview_preserves_navigation_when_page_ocr_or_render_fails(monkeypatch):
    pdf = scanned_pdf(4)
    original_render = worker.render_pdf_page

    def render_with_broken_page(data, page):
        if page == 3:
            raise ValueError("Unexpected page content")
        return original_render(data, page)

    monkeypatch.setattr(worker, "render_pdf_page", render_with_broken_page)
    code, output = worker.read_pdf_preview(pdf, 3)
    payload = json.loads(output)
    assert code == 0
    assert (payload["page"], payload["total_pages"]) == (3, 4)
    assert payload["image"] is None and payload["regions"] == []
    assert "berikutnya" in payload["warning"]
    code, output = worker.read_pdf_preview(pdf, 4)
    assert code == 0 and json.loads(output)["regions"]

    monkeypatch.setattr(worker, "read_image_regions", lambda data, extension: (2, b""))
    code, output = worker.read_pdf_preview(pdf, 2)
    payload = json.loads(output)
    assert code == 0 and payload["image"].startswith("data:image/jpeg;base64,")
    assert payload["regions"] == [] and "OCR lokal" in payload["warning"]


def test_pdf_uses_local_ocr_if_text_parser_fails(monkeypatch):
    from markitdown import MarkItDown

    def fail_pdf_parser(*args, **kwargs):
        raise ValueError("PDF text parser failed")

    monkeypatch.setattr(MarkItDown, "convert_stream", fail_pdf_parser)
    code, output = worker.read_pdf_text(scanned_pdf(1))
    payload = json.loads(output)
    assert code == 0
    assert payload["engine"] == "tesseract-pdf"
    assert "125000" in payload["markdown"].replace(" ", "")


def test_pdf_partial_ocr_marks_failed_page_and_keeps_other_pages(monkeypatch):
    original_render = worker.render_pdf_page

    def render_with_broken_page(data, page):
        if page == 3:
            raise ValueError("Unexpected page content")
        return original_render(data, page)

    monkeypatch.setattr(worker, "render_pdf_page", render_with_broken_page)
    code, output = worker.read_pdf_text(scanned_pdf(4))
    payload = json.loads(output)
    assert code == 0 and payload["engine"] == "tesseract-pdf-partial"
    assert "OCR gagal membaca halaman ini" in payload["markdown"]
    assert "425000" in payload["markdown"].replace(" ", "")


def test_preview_quota_explains_when_to_retry(client):
    region_requests.extend([time.monotonic()] * 12)
    response = client.post("/api/pdf-preview?page=1", content=scanned_pdf(1), headers={"X-Filename": "scan.pdf"})
    assert response.status_code == 429
    assert "detik" in response.json()["detail"]
    assert int(response.headers["retry-after"]) > 0


def test_sumopod_provider_request_and_output(client, monkeypatch):
    monkeypatch.setenv("SUMOPOD_API_KEY", "server-only-test-key")
    assert client.get("/api/capabilities").json() == {"image_ocr": "sumopod", "image_ocr_model": "gpt-4o-mini"}
    seen = {}

    def fake_post(url, *, json, headers, timeout, follow_redirects):
        seen.update(url=url, body=json, headers=headers, timeout=timeout, redirects=follow_redirects)
        return httpx.Response(200, json={"choices": [{"message": {"content": "HELLO 123"}}]})

    monkeypatch.setattr("app.main.httpx.post", fake_post)
    image = Image.new("RGB", (2000, 1000), "white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    response = upload(client, "screenshot.png", buffer.getvalue())
    assert response.status_code == 200, response.text
    assert response.text == "HELLO 123"
    assert response.headers["x-ocr-engine"] == "sumopod"
    assert seen["url"] == "https://ai.sumopod.com/v1/chat/completions"
    assert seen["body"]["model"] == "gpt-4o-mini"
    assert seen["headers"]["Authorization"] == "Bearer server-only-test-key"
    assert seen["redirects"] is False
    assert seen["body"]["messages"][0]["content"][1]["image_url"]["detail"] == "high"
    encoded = seen["body"]["messages"][0]["content"][1]["image_url"]["url"].split(",", 1)[1]
    with Image.open(io.BytesIO(base64.b64decode(encoded))) as sent_image:
        assert max(sent_image.size) == 2000


def test_sumopod_provider_error_does_not_fall_back(client, monkeypatch):
    monkeypatch.setenv("SUMOPOD_API_KEY", "server-only-test-key")
    monkeypatch.setattr("app.main.httpx.post", lambda *a, **k: httpx.Response(401, text="secret provider body"))
    image = Image.new("RGB", (100, 100), "white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    response = upload(client, "screenshot.png", buffer.getvalue())
    assert response.status_code == 503
    assert "secret provider body" not in response.text


def test_vision_prepares_photo_without_truncating_it():
    image = Image.frombytes("RGB", (1288, 1288), os.urandom(1288 * 1288 * 3))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=60)
    prepared = run_worker(buffer.getvalue(), ".jpg", "prepare-vision", timeout=20)
    assert 2 * 1024 * 1024 < len(prepared) < 10 * 1024 * 1024
    with Image.open(io.BytesIO(prepared)) as converted:
        assert converted.format == "PNG"


def test_public_conversion_ignores_old_secret(client, monkeypatch):
    monkeypatch.setenv("WEB_API_KEY", "old-secret")
    assert upload(client, "a.txt", b"hi").status_code == 200


def test_public_quota(client, monkeypatch):
    monkeypatch.setattr("app.main.convert", lambda data, extension: "ok")
    for _ in range(MAX_REQUESTS_PER_MINUTE):
        assert upload(client, "a.txt", b"hi").status_code == 200
    assert upload(client, "a.txt", b"hi").status_code == 429


def test_sumopod_hourly_quota(client, monkeypatch):
    monkeypatch.setenv("SUMOPOD_API_KEY", "server-only-test-key")
    monkeypatch.setattr("app.main.MAX_REQUESTS_PER_MINUTE", 100)
    monkeypatch.setattr("app.main.convert_sumopod", lambda data, extension: "ok")
    for _ in range(30):
        assert upload(client, "a.png", b"\x89PNG\r\n\x1a\nmock").status_code == 200
    assert upload(client, "a.png", b"\x89PNG\r\n\x1a\nmock").status_code == 429


@pytest.mark.parametrize("name,content,expected", [
    ("../../secret.txt", b"hi", 400),
    ("evil.html", b"<h1>Hi</h1>", 415),
    ("fake.pdf", b"This is not a PDF", 415),
    ("empty.txt", b"", 400),
    ("bad.txt", b"\xff", 415),
    ("fake.png", b"not an image", 415),
    ("fake.webp", b"RIFF0000WRONG", 415),
    ("large.txt", b"a" * (10 * 1024 * 1024 + 1), 413),
])
def test_rejects_unsafe_uploads(client, name, content, expected):
    assert upload(client, name, content).status_code == expected


def test_office_zip_is_checked(client):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("something.txt", "no office parts")
    assert upload(client, "fake.docx", buffer.getvalue()).status_code == 415


def test_image_format_and_pixel_limit(client):
    image = Image.new("RGB", (20, 20), "white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    assert upload(client, "wrong.jpg", buffer.getvalue()).status_code == 415

    large = Image.new("RGB", (3000, 3000), "white")
    buffer = io.BytesIO()
    large.save(buffer, format="PNG")
    assert upload(client, "large.png", buffer.getvalue()).status_code == 413
