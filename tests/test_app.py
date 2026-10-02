"""Behavioral checks for the single DeepSeek OCR path and PDF limit."""
import io
import json
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import main as app_main, worker
from app.main import app, recent_requests, region_requests, review_requests


@pytest.fixture
def client(monkeypatch):
    recent_requests.clear()
    region_requests.clear()
    review_requests.clear()
    app_main.tool_requests.clear()
    monkeypatch.delenv("SUMOPOD_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(app_main, "verify_vision", lambda: None)
    monkeypatch.setattr(app_main, "vision_route", "chat")
    monkeypatch.setattr(app_main, "vision_minimal", False)
    return TestClient(app)


def upload(client, name, data, path="/api/convert"):
    return client.post(path, content=data, headers={"X-Filename": name, "X-External-AI": "true"})


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


def test_pdf_text_layer_is_ocr_from_rendered_image(client, monkeypatch):
    monkeypatch.setattr("app.main.httpx.post", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("external OCR called")))
    response = upload(client, "text.pdf", text_layer_pdf_bytes(), "/api/pdf-preview?page=1")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["image"].startswith("data:image/jpeg;base64,")
    assert payload["page_source"] == "tesseract"
    assert "LAPISAN TERBACA" in payload["page_text"]


def test_pdf_ocr_orders_table_rows_by_image_position():
    # Tesseract can report a whole right-hand column before the left one.
    words = [
        {"line": (1, 2, 1, 1), "x": 400, "y": 105, "w": 45, "h": 18, "text": "Second"},
        {"line": (1, 2, 1, 2), "x": 400, "y": 145, "w": 40, "h": 18, "text": "Fourth"},
        {"line": (1, 1, 1, 1), "x": 100, "y": 105, "w": 35, "h": 18, "text": "First"},
        {"line": (1, 1, 1, 2), "x": 100, "y": 145, "w": 35, "h": 18, "text": "Third"},
    ]
    assert worker.order_ocr_words(words) == "First | Second\nThird | Fourth"


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
    assert client.get("/api/capabilities").json() == {"image_ocr": "unavailable", "image_ocr_model": "deepseek-v4.1-flash:netra"}
    monkeypatch.setenv("SUMOPOD_API_KEY", "test-key")
    assert client.get("/api/capabilities").json() == {"image_ocr": "sumopod", "image_ocr_model": "deepseek-v4.1-flash:netra"}
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ignored-key")
    assert client.get("/api/capabilities").json() == {"image_ocr": "sumopod", "image_ocr_model": "deepseek-v4.1-flash:netra"}
    assert "PrivasiDoc" in client.get("/").text
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
    assert upload(client, "scan.pdf", pdf_bytes()).status_code == 200
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
    assert response.headers["x-ocr-engine"] == "deepseek-v4.1-flash:netra"
    regions = upload(client, "gambar.png", image_bytes(), "/api/regions")
    assert regions.status_code == 200
    assert regions.json()["regions"] == [{"x": .1, "y": .2, "w": .5, "h": .1,
                                          "text": "TEKS GAMBAR", "source": "deepseek"}]
    assert len(seen) == 2
    assert all(item[1]["json"]["model"] == "deepseek-v4.1-flash:netra" for item in seen)
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


def test_pdf_ocr_does_not_call_sumopod(client, monkeypatch):
    monkeypatch.setattr("app.main.httpx.post", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("external OCR called")))
    response = upload(client, "text.pdf", text_layer_pdf_bytes(), "/api/pdf-preview?page=1")
    assert response.status_code == 200
    assert response.json()["page_source"] == "tesseract"


def test_blank_pdf_page_reports_ocr_absence(client):
    response = upload(client, "scan.pdf", pdf_bytes(), "/api/pdf-preview?page=1")
    assert response.status_code == 200
    assert response.json()["image"].startswith("data:image/jpeg;base64,")
    assert response.json()["ocr_status"] == 422


def test_pdf_ocr_is_independent_of_box_mapping(client, monkeypatch):
    monkeypatch.setattr("app.main.httpx.post", lambda *a, **kw: (_ for _ in ()).throw(httpx.ReadTimeout("slow")))
    response = upload(client, "text.pdf", text_layer_pdf_bytes(), "/api/pdf-preview?page=1")
    assert response.status_code == 200
    assert response.json()["page_source"] == "tesseract"


def test_empty_provider_result_is_not_reported_as_success(client, monkeypatch):
    monkeypatch.setenv("SUMOPOD_API_KEY", "test-key")
    fake_provider(monkeypatch, markdown="")
    response = upload(client, "gambar.png", image_bytes())
    assert response.status_code == 502 and "OCR kosong" in response.json()["detail"]
    preview = upload(client, "scan.pdf", pdf_bytes(), "/api/pdf-preview?page=1")
    assert preview.status_code == 200 and preview.json()["ocr_status"] == 422
    assert preview.json()["image"].startswith("data:image/jpeg;base64,")


def test_vision_probe_reports_missing_image_response(monkeypatch):
    monkeypatch.setenv("SUMOPOD_API_KEY", "test-key")
    monkeypatch.setattr(app_main.secrets, "choice", lambda choices: "MERAH")
    monkeypatch.setattr(app_main, "vision_probe_ok", False)
    monkeypatch.setattr(app_main, "vision_probe_retry_at", 0.0)
    monkeypatch.setattr(app_main, "request_vision",
                        lambda *args, **kwargs: "Tidak ada gambar" if kwargs["route"] == "chat" else "PADAMU NEGERI")
    with pytest.raises(app_main.HTTPException) as caught:
        app_main.verify_vision()
    assert caught.value.status_code == 503
    assert "SumoPod menyatakan gambar tidak tersedia" in caught.value.detail


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
    assert calls == [{"max_tokens": 4096, "thinking": False, "route": "chat", "minimal": False}]



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


def test_vision_probe_selects_minimal_payload(monkeypatch):
    monkeypatch.setenv("SUMOPOD_API_KEY", "test-key")
    monkeypatch.setattr(app_main.secrets, "choice", lambda choices: "MERAH")
    monkeypatch.setattr(app_main, "vision_probe_ok", False)
    monkeypatch.setattr(app_main, "vision_probe_retry_at", 0.0)
    monkeypatch.setattr(app_main, "vision_minimal", False)
    seen = []

    def probe(image, mime_type, prompt, **kwargs):
        seen.append((kwargs["route"], kwargs["minimal"]))
        return "MERAH" if kwargs["minimal"] and kwargs["route"] == "chat" else "LAIN"

    monkeypatch.setattr(app_main, "request_vision", probe)
    app_main.verify_vision()
    assert seen == [("chat", False), ("responses", False), ("chat", True)]
    assert app_main.vision_route == "chat"
    assert app_main.vision_minimal is True
    monkeypatch.setattr(app_main, "vision_probe_ok", False)


def test_minimal_payload_keeps_image_and_omits_optional_controls(client, monkeypatch):
    monkeypatch.setenv("SUMOPOD_API_KEY", "test-key")
    seen = []

    def fake_post(url, **kwargs):
        seen.append((url, kwargs["json"]))
        if url.endswith("/responses"):
            return httpx.Response(200, json={"status": "completed", "output_text": "MERAH"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "MERAH"}}]})

    monkeypatch.setattr(app_main.httpx, "post", fake_post)
    assert app_main.request_vision(b"PNG", "image/png", "Baca teks", minimal=True) == "MERAH"
    assert app_main.request_vision(b"PNG", "image/png", "Baca teks", route="responses", minimal=True) == "MERAH"
    chat, responses = seen[0][1], seen[1][1]
    assert "thinking" not in chat
    assert "detail" not in chat["messages"][0]["content"][1]["image_url"]
    assert chat["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "reasoning" not in responses
    assert "detail" not in responses["input"][0]["content"][1]
    assert responses["input"][0]["content"][1]["image_url"].startswith("data:image/png;base64,")


def test_pdf_ocr_each_page_from_full_page_images(client, monkeypatch):
    from PIL import ImageDraw, ImageFont
    monkeypatch.setattr("app.main.httpx.post", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("external OCR called")))
    images = []
    for word in ("LAMPIRAN SATU", "LAMPIRAN DUA"):
        image = Image.new("RGB", (850, 1100), "white")
        ImageDraw.Draw(image).text((100, 130), word, fill="black", font=ImageFont.load_default(size=48))
        images.append(image)
    buffer = io.BytesIO()
    images[0].save(buffer, format="PDF", save_all=True, append_images=images[1:])
    pdf = buffer.getvalue()
    assert upload(client, "scan.pdf", pdf).status_code == 200
    for page, word in ((1, "SATU"), (2, "DUA")):
        response = upload(client, "scan.pdf", pdf, f"/api/pdf-preview?page={page}")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["page_source"] == "tesseract"
        assert word in payload["page_text"]
        assert payload["image"].startswith("data:image/jpeg;base64,")
    zoomed = upload(client, "scan.pdf", pdf, "/api/pdf-preview?page=2&zoom=true")
    assert zoomed.status_code == 200
    assert "DUA" in zoomed.json()["page_text"]
    assert len(zoomed.json()["image"]) > len(payload["image"])
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


def test_worker_ocr_receives_rendered_image_only():
    image = worker.render_pdf_page(text_layer_pdf_bytes(), 1)
    assert image.startswith(b"\xff\xd8\xff")
    text, boxes = worker.ocr_rendered_page(image)
    assert "LAPISAN TERBACA" in text
    assert boxes and all(box["source"] == "tesseract" for box in boxes)


def test_pdf_language_review_uses_text_only_and_preserves_ocr(client, monkeypatch):
    monkeypatch.setenv("SUMOPOD_API_KEY", "test-key")
    sent = []

    def provider(url, **kwargs):
        sent.append(kwargs["json"])
        content = json.dumps({"languages": ["Indonesia"], "assessment": "perlu_tinjau",
                              "suspect_spans": ["teks janggal", "kutipan rekaan"]})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]})

    monkeypatch.setattr("app.main.httpx.post", provider)
    text = "Ini adalah teks janggal pada hasil OCR halaman."
    response = client.post("/api/pdf-language-check", json={"text": text}, headers={"X-External-AI": "true"})
    assert response.status_code == 200, response.text
    assert response.json()["suspect_spans"] == ["teks janggal"]
    assert response.json()["scope"] == "text_only"
    assert sent[0]["model"] == "deepseek-v4.1-flash:netra"
    assert text in sent[0]["messages"][0]["content"]
    assert "image_url" not in json.dumps(sent[0])

    monkeypatch.setattr("app.main.httpx.post", lambda *a, **kw: httpx.Response(503))
    unavailable = client.post("/api/pdf-language-check", json={"text": text}, headers={"X-External-AI": "true"})
    assert unavailable.status_code == 502
    pdf = upload(client, "text.pdf", text_layer_pdf_bytes(), "/api/pdf-preview?page=1")
    assert pdf.status_code == 200 and "LAPISAN TERBACA" in pdf.json()["page_text"]
