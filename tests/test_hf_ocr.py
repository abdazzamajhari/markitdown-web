"""Official Hugging Face Space upload, queued SSE and privacy boundaries."""
import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import main, unlimited_ocr as ocr

RealClient = httpx.Client


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.delenv("UNLIMITED_OCR_BACKEND", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    for queue in (main.recent_requests, main.region_requests, main.review_requests):
        queue.clear()
    calls = []
    def install(*, final=None, wire=None, status=200, uploads=None, job=None):
        output = final if final is not None else [{"text": "<|det|>text [0,0,999,99]<|/det|>HASIL HUGGING FACE", "done": True}]
        def handle(request):
            calls.append(request)
            if request.url.path.endswith("/upload"):
                return httpx.Response(status, json=uploads if uploads is not None else ["/tmp/gradio/hash/ocr-input.png"])
            if request.method == "POST":
                return httpx.Response(200, json=job if job is not None else {"event_id": "abc123"})
            data = wire if wire is not None else ('event: heartbeat\ndata: null\n\nevent: generating\ndata: [{"text":"PARTIAL", "done":false}]\n\nevent: complete\ndata: ' + json.dumps(output) + '\n\n').encode()
            return httpx.Response(200, content=data)
        monkeypatch.setattr(ocr.httpx, "Client", lambda **kwargs: RealClient(transport=httpx.MockTransport(handle), **kwargs))
        return calls
    return install, calls


def image_bytes():
    output = io.BytesIO()
    image = Image.new("RGB", (400,200), "white")
    exif = Image.Exif(); exif[315] = "Private metadata"
    image.save(output, "PNG", exif=exif)
    return output.getvalue()


def test_huggingface_is_default_without_endpoint_or_key(setup, monkeypatch):
    install, calls = setup; install()
    monkeypatch.delenv("UNLIMITED_OCR_BASE_URL", raising=False)
    assert ocr.backend() == "huggingface"
    assert ocr.configuration() == (ocr.HF_SPACE, "", ocr.MODEL)
    result = ocr.analyze(b"prepared-image", "image/png")
    assert result["markdown"] == "HASIL HUGGING FACE" and result["regions"][0]["w"] == 1
    assert len(calls) == 3
    assert all(r.url.host == "baidu-unlimited-ocr.hf.space" for r in calls)
    assert "authorization" not in calls[0].headers
    assert b'filename="ocr-input.png"' in calls[0].content
    body = json.loads(calls[1].content)
    assert body == {"image_path": {"path": "/tmp/gradio/hash/ocr-input.png", "meta": {"_type": "gradio.FileData"}, "orig_name": "ocr-input.png"},
                    "mode": "gundam", "prompt": "document parsing."}
    assert calls[-1].url.path == "/gradio_api/call/run_ocr/abc123"


def test_optional_token_is_sent_only_to_fixed_space(setup, monkeypatch):
    install, calls = setup; install()
    monkeypatch.setenv("HF_TOKEN", "private-hf-token")
    ocr.analyze(b"pixels", "image/jpeg")
    assert all(r.headers["authorization"] == "Bearer private-hf-token" for r in calls)
    assert all(r.url.host == "baidu-unlimited-ocr.hf.space" for r in calls)
    assert b'filename="ocr-input.jpg"' in calls[0].content
    assert "private-hf-token" not in json.dumps(TestClient(main.app).get("/api/capabilities").json())


@pytest.mark.parametrize("wire,expected", [
    (b'event: generating\ndata: [{"text":"PARTIAL","done":false}]\n\n',502),
    (b'event: complete\ndata: [{"text":"PARTIAL","done":false}]\n\n',502),
    (b'event: complete\ndata: [{"text":"","done":true}]\n\n',422),
    (b'event: error\ndata: {"error":"Private provider stack with uploaded file"}\n\n',502),
    (b'event: error\ndata: "ZeroGPU quota exceeded"\n\n',429),
    (b'event: complete\ndata: not-json\n\n',502),
    (b'event: complete\ndata: ["wrong-shape"]\n\n',502),
])
def test_partial_error_and_quota_never_become_success(setup, wire, expected):
    install, _ = setup; install(wire=wire)
    with pytest.raises(main.HTTPException) as error:
        ocr.analyze(b"pixels", "image/png")
    assert error.value.status_code == expected
    assert "Private provider" not in error.value.detail
    if expected == 429:
        assert error.value.headers["X-OCR-Retryable"] == "false"


@pytest.mark.parametrize("status,expected", [(401,503),(403,503),(429,429),(402,429),(503,502),(302,502)])
def test_hf_http_failure_stops_before_submission(setup, status, expected):
    install, calls = setup; install(status=status)
    with pytest.raises(main.HTTPException) as error:
        ocr.analyze(b"pixels", "image/png")
    assert error.value.status_code == expected and len(calls) == 1


@pytest.mark.parametrize("uploads,job", [(["https://evil.example/file"],None), ([],None),
                                          (["/tmp/gradio/file"],{"event_id":"../wrong"}),
                                          (["/tmp/gradio/file"],[])])
def test_invalid_uploaded_path_or_event_cannot_change_request_target(setup, uploads, job):
    install, calls = setup; install(uploads=uploads, job=job)
    with pytest.raises(main.HTTPException) as error:
        ocr.analyze(b"pixels", "image/png")
    assert error.value.status_code == 502 and len(calls) < 3


def test_json_image_result_contains_text_and_boxes_in_one_inference(setup):
    install, calls = setup; install()
    client = TestClient(main.app)
    response = client.post("/api/convert", content=image_bytes(), headers={
        "X-Filename": "private-original-name.png", "X-External-AI": "true", "Accept": "application/json"})
    assert response.status_code == 200, response.text
    assert response.json()["markdown"] == "HASIL HUGGING FACE"
    assert response.json()["regions"][0]["source"] == "unlimited-ocr"
    assert len(calls) == 3 and b"private-original-name" not in calls[0].content
    assert b"Private metadata" not in calls[0].content
    assert response.headers["cache-control"] == "no-store"
    cap = client.get("/api/capabilities").json()
    assert cap["ocr_provider"] == "huggingface" and cap["pdf_ocr"] == "unlimited-ocr"


def test_hf_consent_blocks_upload(setup):
    install, calls = setup; install()
    response = TestClient(main.app).post("/api/convert", content=image_bytes(), headers={"X-Filename":"a.png"})
    assert response.status_code == 400 and not calls


def test_stream_frames_split_across_chunks_and_crlf(setup, monkeypatch):
    class Chunks(httpx.SyncByteStream):
        def __iter__(self):
            raw = 'event: complete\r\ndata: [{"text":"Bahasa Indonesia 中文","done":true}]\r\n\r\n'.encode()
            for byte in raw:
                yield bytes([byte])
    count = 0
    def handle(request):
        nonlocal count
        count += 1
        if count == 1: return httpx.Response(200,json=["/tmp/gradio/input"])
        if count == 2: return httpx.Response(200,json={"event_id":"event1"})
        return httpx.Response(200,stream=Chunks())
    monkeypatch.setattr(ocr.httpx,"Client",lambda **kwargs: RealClient(transport=httpx.MockTransport(handle),**kwargs))
    assert ocr.analyze(b"pixels","image/png")["markdown"] == "Bahasa Indonesia 中文"


def test_heartbeat_stream_obeys_total_deadline(setup, monkeypatch):
    clock = [0.0]
    received = []
    class Heartbeats(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(100):
                clock[0] += 15
                received.append(1)
                yield b"event: heartbeat\ndata: null\n\n"
    def handle(request):
        if request.url.path.endswith('/upload'): return httpx.Response(200,json=['/tmp/gradio/input'])
        if request.method == 'POST': return httpx.Response(200,json={'event_id':'event1'})
        return httpx.Response(200,stream=Heartbeats())
    monkeypatch.setattr(ocr.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(ocr.httpx,'Client',lambda **kwargs:RealClient(transport=httpx.MockTransport(handle),**kwargs))
    with pytest.raises(main.HTTPException) as error:
        ocr.analyze(b'pixels','image/png')
    assert error.value.status_code == 504 and len(received) <= 8
