"""Bounded Unlimited-OCR clients: official Hugging Face Space and optional vLLM."""

import base64
import json
import math
import os
import re
import time
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException

MODEL = "baidu/Unlimited-OCR"
HF_SPACE = "https://baidu-unlimited-ocr.hf.space"
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_STREAM_BYTES = 32 * 1024 * 1024
# One request contains one image, including each separately rendered PDF page.
PROMPT = "<image>document parsing."


def backend() -> str:
    selected = os.environ.get("UNLIMITED_OCR_BACKEND", "huggingface").strip().lower()
    if selected not in {"huggingface", "vllm"}:
        raise HTTPException(503, "Pilihan layanan Unlimited-OCR tidak valid")
    return selected


def configuration() -> tuple[str, str, str]:
    if backend() == "huggingface":
        return HF_SPACE, os.environ.get("HF_TOKEN", "").strip(), MODEL
    base = os.environ.get("UNLIMITED_OCR_BASE_URL", "").strip().rstrip("/")
    try:
        parsed = urlsplit(base)
        parsed.port  # Validate malformed port/IPv6 configurations before HTTP use.
    except ValueError:
        raise HTTPException(503, "URL server Unlimited-OCR tidak valid") from None
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or
            parsed.scheme not in {"http", "https"} or (parsed.scheme == "http" and not local)):
        raise HTTPException(503, "Server Unlimited-OCR belum dikonfigurasi. Atur UNLIMITED_OCR_BASE_URL ke endpoint GPU HTTPS /v1.")
    model = os.environ.get("UNLIMITED_OCR_MODEL", MODEL).strip()
    if not model or len(model) > 200:
        raise HTTPException(503, "Konfigurasi nama model Unlimited-OCR tidak valid")
    return base + "/chat/completions", os.environ.get("UNLIMITED_OCR_API_KEY", "").strip(), model


def require_consent(request) -> None:
    if request.headers.get("x-external-ai") != "true":
        raise HTTPException(400, "Izinkan pengiriman gambar ke server Unlimited-OCR sebelum menjalankan OCR")


def parse_output(raw: str) -> dict:
    """Decode both released grounding formats, keeping model reading order."""
    marker_pattern = r"(?:<\|ref\|>([^<]*)<\|/ref\|>\s*)?<\|det\|>(.*?)<\|/det\|>"
    markers = list(re.finditer(marker_pattern, raw, re.DOTALL))
    regions, blocks = [], []
    if markers:
        blocks.append(raw[:markers[0].start()])
    else:
        blocks.append(raw)
    for index, marker in enumerate(markers):
        inside = marker.group(2).strip()
        label = re.match(r"([A-Za-z_][\w-]*)\s*", inside)
        kind = (marker.group(1) or (label.group(1) if label else "")).strip().lower()
        end = markers[index + 1].start() if index + 1 < len(markers) else len(raw)
        text = raw[marker.end():end].strip()
        if kind in {"image", "figure"}:
            continue
        if text:
            blocks.append(text)
        # Upstream modeling_unlimitedocr.py normalizes coordinates by 999.
        # Invalid grounding must not discard a valid transcription.
        start = inside.find("[")
        try:
            boxes = json.loads(inside[start:]) if start >= 0 else []
            if not isinstance(boxes, list):
                boxes = []
            if boxes and isinstance(boxes[0], (int, float)):
                boxes = [boxes]
        except (ValueError, TypeError):
            boxes = []
        for box in boxes[:600]:
            if not text or len(regions) >= 600:
                break
            if not isinstance(box, list) or len(box) != 4 or any(type(v) not in {int, float} for v in box):
                continue
            x1, y1, x2, y2 = box
            if 0 <= x1 < x2 <= 999 and 0 <= y1 < y2 <= 999 and all(math.isfinite(v) for v in box):
                regions.append({"x": x1 / 999, "y": y1 / 999,
                                "w": (x2 - x1) / 999, "h": (y2 - y1) / 999,
                                "text": re.sub(r"<\|[^>]+\|>", "", text).strip()[:300],
                                "source": "unlimited-ocr"})
    markdown = "\n\n".join(blocks)
    markdown = re.sub(r"<\|[^>]+\|>", "", markdown)
    markdown = markdown.replace("<｜end▁of▁sentence｜>", "").strip()
    if not markdown:
        raise HTTPException(422, "Unlimited-OCR tidak menemukan teks. Periksa gambar atau coba OCR diperbesar.")
    return {"markdown": markdown, "regions": regions}


def analyze(image: bytes, mime_type: str) -> dict:
    url, key, model = configuration()
    if backend() == "huggingface":
        return analyze_huggingface(image, mime_type, key)
    if mime_type not in {"image/png", "image/jpeg"} or not image or len(image) > 10 * 1024 * 1024:
        raise HTTPException(413, "Gambar tidak valid atau terlalu besar untuk Unlimited-OCR")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64," + base64.b64encode(image).decode("ascii")}},
        ]}],
        "temperature": 0.0, "max_tokens": 8192, "stream": False,
        "skip_special_tokens": False,
        "vllm_xargs": {"ngram_size": 35, "window_size": 128},
    }
    headers = {"Authorization": "Bearer " + key} if key else {}
    started = time.monotonic()
    try:
        with httpx.stream("POST", url, json=payload, headers=headers,
                          timeout=httpx.Timeout(120, connect=10), follow_redirects=False) as response:
            if response.status_code in {401, 403}:
                raise HTTPException(503, "Autentikasi server Unlimited-OCR gagal; periksa API key")
            if response.status_code == 429:
                raise HTTPException(429, "Antrean Unlimited-OCR penuh; coba lagi nanti", headers={"Retry-After": "60"})
            if response.status_code != 200:
                raise HTTPException(502, f"Server Unlimited-OCR mengembalikan HTTP {response.status_code}")
            body = bytearray()
            for chunk in response.iter_bytes():
                if time.monotonic() - started > 120:
                    raise HTTPException(504, "Unlimited-OCR melewati batas waktu 120 detik")
                if len(body) + len(chunk) > MAX_OUTPUT_BYTES + 65536:
                    raise HTTPException(502, "Respons Unlimited-OCR terlalu besar")
                body.extend(chunk)
    except httpx.TimeoutException:
        raise HTTPException(504, "Unlimited-OCR melewati batas waktu 120 detik") from None
    except httpx.HTTPError:
        raise HTTPException(502, "Server Unlimited-OCR tidak dapat dihubungi") from None
    try:
        reply = json.loads(body)
        choice = reply["choices"][0]
        if not isinstance(choice, dict):
            raise ValueError
        if choice.get("finish_reason") == "length":
            raise HTTPException(502, "Hasil Unlimited-OCR terpotong; keluaran parsial tidak digunakan")
        content = choice["message"]["content"]
        if choice.get("finish_reason") != "stop" or not isinstance(content, str) or reply.get("model") != model:
            raise ValueError
        content.encode("utf-8")  # Reject malformed Unicode before constructing API output.
    except (KeyError, IndexError, TypeError, ValueError, UnicodeError):
        raise HTTPException(502, "Respons atau nama model Unlimited-OCR tidak sesuai") from None
    result = parse_output(content)
    if len(result["markdown"].encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise HTTPException(413, "Hasil OCR terlalu besar")
    return result


def _hf_http_error(response) -> None:
    if response.status_code in {401, 403}:
        raise HTTPException(503, "Akses Hugging Face ditolak; periksa HF_TOKEN pada server aplikasi")
    if response.status_code in {402, 429}:
        raise HTTPException(429, "Kuota atau antrean GPU Hugging Face sedang penuh. Coba lagi setelah kuota tersedia.",
                            headers={"Retry-After": "60", "X-OCR-Retryable": "false"})
    if response.status_code != 200:
        raise HTTPException(502, f"Space Unlimited-OCR Hugging Face mengembalikan HTTP {response.status_code}")


def _hf_json(client, url: str, deadline: float, **kwargs):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise HTTPException(504, "Antrean atau OCR Hugging Face melewati batas waktu 120 detik")
    with client.stream("POST", url, timeout=httpx.Timeout(remaining, connect=min(10, remaining)), **kwargs) as response:
        _hf_http_error(response)
        body = bytearray()
        for chunk in response.iter_bytes():
            if time.monotonic() >= deadline:
                raise HTTPException(504, "Antrean atau OCR Hugging Face melewati batas waktu 120 detik")
            if len(body) + len(chunk) > 65536:
                raise HTTPException(502, "Respons antrean Hugging Face terlalu besar")
            body.extend(chunk)
    try:
        return json.loads(body)
    except (ValueError, UnicodeError):
        raise HTTPException(502, "Respons antrean Hugging Face tidak valid") from None


def _hf_event(frame: bytes):
    try:
        lines = frame.decode("utf-8").splitlines()
        event = next((line[6:].strip() for line in lines if line.startswith("event:")), "")
        data = "\n".join(line[5:].lstrip() for line in lines if line.startswith("data:"))
        if event in {"error", "complete"}:
            return event, json.loads(data)
    except (ValueError, UnicodeError):
        raise HTTPException(502, "Format hasil streaming Hugging Face tidak valid") from None
    return None, None


def _hf_complete(value) -> dict:
    if (not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict) or
            value[0].get("done") is not True or not isinstance(value[0].get("text"), str)):
        raise HTTPException(502, "Hugging Face belum mengembalikan hasil OCR lengkap")
    text = value[0]["text"]
    try:
        size = len(text.encode("utf-8"))
    except UnicodeError:
        raise HTTPException(502, "Teks Hugging Face tidak valid") from None
    if size > MAX_OUTPUT_BYTES:
        raise HTTPException(413, "Hasil OCR terlalu besar")
    return parse_output(text)


def analyze_huggingface(image: bytes, mime_type: str, token: str = "") -> dict:
    """Upload only normalized pixels, submit one page, consume a final SSE event."""
    if mime_type not in {"image/png", "image/jpeg"} or not image or len(image) > 10 * 1024 * 1024:
        raise HTTPException(413, "Gambar tidak valid atau terlalu besar untuk Unlimited-OCR")
    headers = {"Authorization": "Bearer " + token} if token else {}
    deadline = time.monotonic() + 120
    filename = "ocr-input.png" if mime_type == "image/png" else "ocr-input.jpg"
    try:
        with httpx.Client(headers=headers, follow_redirects=False) as client:
            paths = _hf_json(client, HF_SPACE + "/gradio_api/upload", deadline,
                             files={"files": (filename, image, mime_type)})
            if (not isinstance(paths, list) or len(paths) != 1 or not isinstance(paths[0], str) or
                    not paths[0].startswith("/") or len(paths[0]) > 4096 or re.search(r"[\x00-\x1f]", paths[0])):
                raise HTTPException(502, "Respons unggahan Hugging Face tidak valid")
            job = _hf_json(client, HF_SPACE + "/gradio_api/call/v2/run_ocr", deadline, json={
                "image_path": {"path": paths[0], "meta": {"_type": "gradio.FileData"}, "orig_name": filename},
                "mode": "gundam", "prompt": "document parsing.",
            })
            event_id = job.get("event_id") if isinstance(job, dict) else None
            if not isinstance(event_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", event_id):
                raise HTTPException(502, "ID antrean Hugging Face tidak valid")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HTTPException(504, "Antrean atau OCR Hugging Face melewati batas waktu 120 detik")
            with client.stream("GET", HF_SPACE + "/gradio_api/call/run_ocr/" + event_id,
                               timeout=httpx.Timeout(remaining, connect=min(10, remaining))) as response:
                _hf_http_error(response)
                pending = bytearray()
                received = 0
                for chunk in response.iter_bytes():
                    received += len(chunk)
                    if time.monotonic() >= deadline:
                        raise HTTPException(504, "Antrean atau OCR Hugging Face melewati batas waktu 120 detik")
                    if received > MAX_STREAM_BYTES:
                        raise HTTPException(502, "Streaming Hugging Face melebihi batas ukuran")
                    pending.extend(chunk)
                    while True:
                        boundary = re.search(rb"\r?\n\r?\n", pending)
                        if not boundary:
                            break
                        frame = bytes(pending[:boundary.start()])
                        del pending[:boundary.end()]
                        if len(frame) > MAX_OUTPUT_BYTES + 65536:
                            raise HTTPException(502, "Hasil streaming Hugging Face terlalu besar")
                        event, value = _hf_event(frame)
                        if event == "error":
                            # Provider traces may contain uploads; expose a safe summary only.
                            if re.search(r"quota|zerogpu|gpu.*limit|rate.?limit", json.dumps(value), re.I):
                                raise HTTPException(429, "Kuota GPU Hugging Face habis atau sementara tidak tersedia. Coba lagi setelah kuota tersedia.",
                                                    headers={"Retry-After": "60", "X-OCR-Retryable": "false"})
                            raise HTTPException(502, "Space Unlimited-OCR Hugging Face gagal memproses gambar. Coba lagi nanti.")
                        if event == "complete":
                            return _hf_complete(value)
                    if len(pending) > MAX_OUTPUT_BYTES + 65536:
                        raise HTTPException(502, "Hasil streaming Hugging Face terlalu besar")
    except httpx.TimeoutException:
        raise HTTPException(504, "Antrean atau OCR Hugging Face melewati batas waktu 120 detik") from None
    except httpx.HTTPError:
        raise HTTPException(502, "Space Unlimited-OCR Hugging Face tidak dapat dihubungi") from None
    raise HTTPException(502, "Koneksi Hugging Face berakhir sebelum hasil OCR lengkap diterima")
