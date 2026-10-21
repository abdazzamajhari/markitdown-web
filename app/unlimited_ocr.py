"""Bounded client for the official Unlimited-OCR vLLM serving recipe."""

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
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
# One request contains one image, including each separately rendered PDF page.
PROMPT = "<image>document parsing."


def configuration() -> tuple[str, str, str]:
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
        if choice.get("finish_reason") == "length":
            raise HTTPException(502, "Hasil Unlimited-OCR terpotong; keluaran parsial tidak digunakan")
        content = choice["message"]["content"]
        if choice.get("finish_reason") != "stop" or not isinstance(content, str) or reply.get("model") != model:
            raise ValueError
    except (KeyError, IndexError, TypeError, ValueError):
        raise HTTPException(502, "Respons atau nama model Unlimited-OCR tidak sesuai") from None
    result = parse_output(content)
    if len(result["markdown"].encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise HTTPException(413, "Hasil OCR terlalu besar")
    return result
