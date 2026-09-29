"""Behavioral checks for the single DeepSeek OCR path and PDF limit."""
import io
import json
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import main as app_main, worker
from app.main import app, recent_requests, region_requests


@pytest.fixture
def client(monkeypatch):
    recent_requests.clear()
    region_requests.clear()
    monkeypatch.delenv("SUMOPOD_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(app_main, "verify_vision", lambda: None)
    monkeypatch.setattr(app_main, "vision_route", "chat")
    return TestClient(app)


def upload(client, name, data, path="/api/convert"):
    return client.post(path, content=data, headers={"X-Filename": name})


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


def test_pdf_text_layer_remains_visible_when_ocr_fails(client, monkeypatch):
    monkeypatch.setenv("SUMOPOD_API_KEY", "test-key")
    monkeypatch.setattr("app.main.httpx.post", lambda *args, **kwargs: httpx.Response(402))
    pdf = text_layer_pdf_bytes()
    response = upload(client, "text.pdf", pdf, "/api/pdf-preview?page=1")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ocr_status"] == 402
    assert payload["page_source"] == "pdf-text"
    assert "LAPISAN TERBACA" in payload["page_text"]
    assert any("LAPISAN TERBACA" in region["text"] for region in payload["regions"])
    preview = upload(client, "text.pdf", pdf, "/api/pdf-preview?page=1&preview_only=true")
    assert preview.status_code == 200
    assert preview.json()["page_text"] == payload["page_text"]


def fake_provider(monkeypatch, markdown="TEKS GAMBAR", regions=None):
    requests = []

    def post(url, **kwargs):
        requests.append((url, kwargs))
        answer = {"markdown": markdown, "regions": regions or []}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer)}}]})

    monkeypatch.setattr("app.main.httpx.post", post)
    return requests


def test_home_and_configuration(client, monkeypatch):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/api/capabilities").json() == {"image_ocr": "unavailable", "image_ocr_model": "deepseek-v4-flash-vision-exp"}
    monkeypatch.setenv("SUMOPOD_API_KEY", "test-key")
    assert client.get("/api/capabilities").json() == {"image_ocr": "sumopod", "image_ocr_model": "deepseek-v4-flash-vision-exp"}
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ignored-key")
    assert client.get("/api/capabilities").json() == {"image_ocr": "sumopod", "image_ocr_model": "deepseek-v4-flash-vision-exp"}
    assert "MarkItDown Web" in client.get("/").text
    assert client.get("/static/app.js").headers["cache-control"] == "no-cache"


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


def test_validates_uploads(client):
    assert upload(client, "../../secret.txt", b"x").status_code == 400
    assert upload(client, "malware.exe", b"x").status_code == 415
    assert upload(client, "empty.txt", b"").status_code == 400
    assert upload(client, "wrong.pdf", b"not a pdf").status_code == 415
    assert upload(client, "wrong.png", b"not png").status_code == 415
    assert upload(client, "image.png", image_bytes()).status_code == 503
    assert upload(client, "scan.pdf", pdf_bytes()).status_code == 503
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as file:
        file.writestr("oops", "not a docx")
    assert upload(client, "wrong.docx", archive.getvalue()).status_code == 415


def test_deepseek_is_only_image_ocr_and_region_source(client, monkeypatch):
    monkeypatch.setenv("SUMOPOD_API_KEY", "test-key")
    seen = fake_provider(monkeypatch, regions=[
        {"x": .1, "y": .2, "w": .5, "h": .1, "text": "TEKS GAMBAR"},
        {"x": 2, "y": 0, "w": .1, "h": .1, "text": "invalid"},
    ])
    response = upload(client, "gambar.png", image_bytes())
    assert response.status_code == 200 and response.text == "TEKS GAMBAR"
    assert response.headers["x-ocr-engine"] == "deepseek-v4-flash-vision-exp"
    regions = upload(client, "gambar.png", image_bytes(), "/api/regions")
    assert regions.status_code == 200
    assert regions.json()["regions"] == [{"x": .1, "y": .2, "w": .5, "h": .1,
                                          "text": "TEKS GAMBAR", "source": "deepseek"}]
    assert len(seen) == 2
    assert all(item[1]["json"]["model"] == "deepseek-v4-flash-vision-exp" for item in seen)
    assert all(item[1]["json"]["thinking"] == {"type": "disabled"} and "reasoning_effort" not in item[1]["json"] for item in seen)
    assert all(item[0] == "https://ai.sumopod.com/v1/chat/completions" for item in seen)
    assert seen[0][1]["json"]["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_only_sumopod_key_is_used(client, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ignored-key")
    assert upload(client, "gambar.png", image_bytes()).status_code == 503
    monkeypatch.setenv("SUMOPOD_API_KEY", "proxy-key")
    seen = fake_provider(monkeypatch)
    assert upload(client, "gambar.png", image_bytes()).status_code == 200
    assert seen[0][0] == "https://ai.sumopod.com/v1/chat/completions"
    assert seen[0][1]["headers"]["Authorization"] == "Bearer proxy-key"


def test_deepseek_plain_text_and_provider_failure(client, monkeypatch):
    monkeypatch.setenv("SUMOPOD_API_KEY", "test-key")
    monkeypatch.setattr("app.main.httpx.post", lambda *a, **kw: httpx.Response(
        200, json={"choices": [{"message": {"content": "TEKS SAJA"}}]}))
    assert upload(client, "gambar.png", image_bytes()).text == "TEKS SAJA"
    monkeypatch.setattr("app.main.httpx.post", lambda *a, **kw: httpx.Response(400))
    response = upload(client, "gambar.png", image_bytes())
    assert response.status_code == 502 and "HTTP 400" in response.json()["detail"]


def test_payment_failure_keeps_pdf_preview_without_fake_ocr(client, monkeypatch):
    monkeypatch.setenv("SUMOPOD_API_KEY", "test-key")
    calls = []

    def payment_required(url, **kwargs):
        calls.append(url)
        return httpx.Response(402)

    monkeypatch.setattr("app.main.httpx.post", payment_required)
    pdf = pdf_bytes(2)
    response = upload(client, "scan.pdf", pdf, "/api/pdf-preview?page=1")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["image"].startswith("data:image/jpeg;base64,")
    assert payload["total_pages"] == 2 and payload["page_text"] == ""
    assert payload["page_source"] is None and payload["ocr_status"] == 402
    assert "SumoPod menolak OCR" in payload["ocr_error"]
    assert len(calls) == 1

    second = upload(client, "scan.pdf", pdf, "/api/pdf-preview?page=2&preview_only=true")
    assert second.status_code == 200
    assert second.json()["image"].startswith("data:image/jpeg;base64,")
    assert second.json()["preview_only"] is True
    assert len(calls) == 1

    image = upload(client, "gambar.png", image_bytes())
    assert image.status_code == 402 and "pembayaran atau kredit" in image.json()["detail"]
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ignored-key")
    again = upload(client, "gambar.png", image_bytes())
    assert again.status_code == 402 and "SumoPod menolak OCR" in again.json()["detail"]
    assert all(url == "https://ai.sumopod.com/v1/chat/completions" for url in calls)


def test_sumopod_timeout_preserves_preview_and_identifies_timeout(client, monkeypatch):
    monkeypatch.setenv("SUMOPOD_API_KEY", "test-key")
    monkeypatch.setattr("app.main.httpx.post",
                        lambda *args, **kwargs: (_ for _ in ()).throw(httpx.ReadTimeout("slow")))
    response = upload(client, "scan.pdf", pdf_bytes(), "/api/pdf-preview?page=1")
    assert response.status_code == 200
    payload = response.json()
    assert payload["image"].startswith("data:image/jpeg;base64,")
    assert payload["ocr_status"] == 504
    assert "80 detik" in payload["ocr_error"]



def test_pdf_transcription_survives_box_mapping_timeout(client, monkeypatch):
    monkeypatch.setenv("SUMOPOD_API_KEY", "test-key")
    calls = []

    def provider(url, **kwargs):
        calls.append(kwargs["json"]["messages"][0]["content"][0]["text"])
        if len(calls) == 1:
            return httpx.Response(200, json={"choices": [{"message": {"content": "HALAMAN OCR"}}]})
        raise httpx.ReadTimeout("coordinate mapping slow")

    monkeypatch.setattr("app.main.httpx.post", provider)
    response = upload(client, "scan.pdf", pdf_bytes(), "/api/pdf-preview?page=1")
    assert response.status_code == 200
    payload = response.json()
    assert payload["page_text"] == "HALAMAN OCR"
    assert payload["page_source"] == "deepseek"
    assert "ocr_error" not in payload
    assert len(calls) == 2



def test_empty_provider_result_is_not_reported_as_success(client, monkeypatch):
    monkeypatch.setenv("SUMOPOD_API_KEY", "test-key")
    fake_provider(monkeypatch, markdown="")
    response = upload(client, "gambar.png", image_bytes())
    assert response.status_code == 502 and "OCR kosong" in response.json()["detail"]
    preview = upload(client, "scan.pdf", pdf_bytes(), "/api/pdf-preview?page=1")
    assert preview.status_code == 200 and preview.json()["ocr_status"] == 502
    assert "OCR kosong" in preview.json()["ocr_error"]
    assert preview.json()["image"].startswith("data:image/jpeg;base64,")


def test_rejects_provider_that_ignores_image(monkeypatch):
    monkeypatch.setenv("SUMOPOD_API_KEY", "test-key")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(app_main, "request_vision", lambda *args, **kwargs: "Unrelated invented text")
    monkeypatch.setattr(app_main, "vision_probe_ok", False)
    monkeypatch.setattr(app_main, "vision_probe_retry_at", 0.0)
    with pytest.raises(app_main.HTTPException) as caught:
        app_main.verify_vision()
    assert caught.value.status_code == 503
    assert "tidak cocok dengan gambar uji" in caught.value.detail



def test_vision_probe_reads_simple_word_without_reasoning(monkeypatch):
    monkeypatch.setenv("SUMOPOD_API_KEY", "test-key")
    monkeypatch.setattr(app_main.secrets, "choice", lambda choices: "KUCING")
    monkeypatch.setattr(app_main, "vision_probe_ok", False)
    monkeypatch.setattr(app_main, "vision_probe_retry_at", 0.0)
    calls = []

    def probe(image, mime_type, prompt, **kwargs):
        calls.append(kwargs)
        return "KUCING"

    monkeypatch.setattr(app_main, "request_vision", probe)
    app_main.verify_vision()
    assert calls == [{"max_tokens": 4096, "thinking": False, "route": "chat"}]



def test_vision_probe_tries_responses_when_chat_ignores_image(monkeypatch):
    monkeypatch.setenv("SUMOPOD_API_KEY", "test-key")
    monkeypatch.setattr(app_main.secrets, "choice", lambda choices: "KUCING")
    monkeypatch.setattr(app_main, "vision_probe_ok", False)
    monkeypatch.setattr(app_main, "vision_probe_retry_at", 0.0)
    monkeypatch.setattr(app_main, "vision_route", "chat")
    seen = []

    def probe(image, mime_type, prompt, **kwargs):
        seen.append((mime_type, kwargs["route"]))
        return "DOKUMEN" if kwargs["route"] == "chat" else "KUCING"

    monkeypatch.setattr(app_main, "request_vision", probe)
    app_main.verify_vision()
    assert seen == [("image/png", "chat"), ("image/png", "responses")]
    assert app_main.vision_route == "responses"
    monkeypatch.setattr(app_main, "vision_probe_ok", False)


def test_responses_route_sends_inline_image_and_reads_output(client, monkeypatch):
    monkeypatch.setenv("SUMOPOD_API_KEY", "test-key")
    seen = []

    def fake_post(url, **kwargs):
        seen.append((url, kwargs["json"]))
        return httpx.Response(200, json={
            "status": "completed",
            "output": [{"content": [{"type": "output_text", "text": "KUCING"}]}],
        })

    monkeypatch.setattr(app_main.httpx, "post", fake_post)
    answer = app_main.request_vision(b"PNG", "image/png",
                                     "Baca teks", route="responses")
    assert answer == "KUCING"
    assert seen[0][0] == "https://ai.sumopod.com/v1/responses"
    assert seen[0][1]["input"][0]["content"][1]["image_url"].startswith("data:image/png;base64,")
    assert seen[0][1]["reasoning"] == {"effort": "none"}


def test_pdf_ocr_each_page_and_boxes(client, monkeypatch):
    monkeypatch.setenv("SUMOPOD_API_KEY", "test-key")
    sent = fake_provider(monkeypatch, regions=[
        {"x": .2, "y": .3, "w": .4, "h": .08, "text": "TEKS GAMBAR"},
    ])
    pdf = pdf_bytes(2)
    initial = upload(client, "scan.pdf", pdf)
    assert initial.status_code == 200, initial.text
    assert not sent
    for page in (1, 2):
        response = upload(client, "scan.pdf", pdf, f"/api/pdf-preview?page={page}")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["page"] == page and payload["total_pages"] == 2
        assert payload["page_text"] == "TEKS GAMBAR" and payload["page_source"] == "deepseek"
        assert payload["regions"][0]["source"] == "deepseek"
        assert payload["image"].startswith("data:image/jpeg;base64,")
    assert len(sent) == 2
    assert upload(client, "scan.pdf", pdf, "/api/pdf-preview?page=3").status_code == 400


def test_pdf_thirty_pages_allowed_and_thirty_one_skipped(client, monkeypatch):
    monkeypatch.setenv("SUMOPOD_API_KEY", "test-key")
    pdf30, pdf31 = pdf_bytes(30), pdf_bytes(31)
    assert worker.pdf_pages(pdf30) == 30
    assert worker.read_pdf_preview(pdf30, 1)[0] == 0
    assert worker.read_pdf_text(pdf31)[0] == 8
    for _ in range(2):
        response = upload(client, "too-many.pdf", pdf31)
        assert response.status_code == 413
        assert response.json()["detail"] == "Dilewati: PDF melebihi 30 halaman"
    assert upload(client, "too-many.pdf", pdf31, "/api/pdf-preview?page=1").status_code == 413
    assert upload(client, "next.txt", b"berikutnya").status_code == 200


def test_worker_has_no_tesseract_dependency():
    import inspect
    assert "tesseract" not in inspect.getsource(worker).lower()
    assert worker.prepare_image(image_bytes(), ".png")[0] == 0
