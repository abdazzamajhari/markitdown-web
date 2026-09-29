"""Public, bounded upload API and browser interface."""

import asyncio
import base64
import json
import logging
import math
import os
import re
import secrets
import subprocess
import sys
import time
import zipfile
from collections import deque
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool

ROOT = Path(__file__).resolve().parent
IMAGE_FORMATS = {".png": b"\x89PNG\r\n\x1a\n", ".jpg": b"\xff\xd8\xff", ".jpeg": b"\xff\xd8\xff", ".webp": b"RIFF"}
ALLOWED = {".pdf", ".docx", ".pptx", ".xlsx", ".txt", ".csv", ".json", *IMAGE_FORMATS}
OFFICE_MARKERS = {
    ".docx": "word/document.xml",
    ".pptx": "ppt/presentation.xml",
    ".xlsx": "xl/workbook.xml",
}
MAX_BYTES = 10 * 1024 * 1024
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_UNZIPPED_BYTES = 40 * 1024 * 1024
TIMEOUT_SECONDS = 30
MAX_REQUESTS_PER_MINUTE = 12
SUMOPOD_URL = "https://ai.sumopod.com/v1/chat/completions"
SUMOPOD_RESPONSES_URL = "https://ai.sumopod.com/v1/responses"
SUMOPOD_MODEL = "deepseek-v4.1-flash:netra"
vision_probe_ok = False
vision_probe_retry_at = 0.0
vision_route = "chat"
vision_minimal = False
OCR_PROMPT = (
    "Transcribe every visible word in this document image, including tables and text inside pictures. "
    "Preserve spelling, language, numbers and reading order. Format tables as Markdown. "
    "Return only the complete Markdown transcription. Do not summarize, describe, or invent text."
)
REGIONS_PROMPT = (
    "Locate the visible text lines in this image. Return only compact JSON with a regions array. "
    "Each region has x, y, w, h normalized between 0 and 1, and exact text. "
    "Omit areas whose coordinates are uncertain."
)

app = FastAPI(title="MarkItDown Web", version="1.0.0", docs_url=None, redoc_url=None)
slots = asyncio.Semaphore(1)
quota_lock = asyncio.Lock()
recent_requests: deque[float] = deque()
region_requests: deque[float] = deque()


def validate_filename(raw: str | None) -> tuple[str, str]:
    if not raw or len(raw) > 384:
        raise HTTPException(400, "Nama berkas tidak valid")
    name = unquote(raw)
    if (len(name) > 128 or name in {".", ".."} or
        "/" in name or "\\" in name or re.search(r"[\x00-\x1f\x7f]", name)):
        raise HTTPException(400, "Nama berkas tidak valid")
    extension = Path(name).suffix.lower()
    if extension not in ALLOWED:
        raise HTTPException(415, "Format berkas tidak didukung")
    return name, extension


def validate_content(data: bytes, extension: str) -> None:
    if not data:
        raise HTTPException(400, "Berkas kosong")
    if extension in IMAGE_FORMATS:
        signature = IMAGE_FORMATS[extension]
        if not data.startswith(signature) or (extension == ".webp" and data[8:12] != b"WEBP"):
            raise HTTPException(415, "Isi berkas tidak cocok dengan format gambar")
    if extension == ".pdf" and not data[:1024].lstrip().startswith(b"%PDF-"):
        raise HTTPException(415, "Isi berkas tidak cocok dengan format PDF")
    if extension in OFFICE_MARKERS:
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                entries = archive.infolist()
                if (len(entries) > 1000 or
                    sum(info.file_size for info in entries) > MAX_UNZIPPED_BYTES or
                    any(info.flag_bits & 1 for info in entries) or
                    OFFICE_MARKERS[extension] not in archive.namelist()):
                    raise HTTPException(415, "Arsip Office tidak valid atau terlalu besar")
        except (zipfile.BadZipFile, ValueError):
            raise HTTPException(415, "Arsip Office tidak valid") from None
    if extension in {".txt", ".csv", ".json"}:
        try:
            data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise HTTPException(415, "Berkas teks harus menggunakan UTF-8") from None


def run_worker(data: bytes, extension: str, mode: str | None = None, timeout: int = TIMEOUT_SECONDS) -> bytes:
    # No shell, no user-controlled paths/URLs, no inherited credentials.
    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
           "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1",
           "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "OMP_THREAD_LIMIT": "1",
           "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1"}
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "app.worker", extension, *([mode] if mode else [])],
            input=data, capture_output=True, timeout=timeout,
            check=False, env=env,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Konversi melebihi batas waktu") from None
    if proc.returncode == 3:
        raise HTTPException(413, "Hasil konversi terlalu besar")
    if proc.returncode == 4:
        raise HTTPException(413, "Resolusi gambar melebihi 8 megapiksel")
    if proc.returncode == 5:
        raise HTTPException(415, "Gambar tidak valid atau formatnya tidak sesuai")
    if proc.returncode == 8:
        raise HTTPException(413, "Dilewati: PDF melebihi 30 halaman")
    if proc.returncode == 7:
        raise HTTPException(400, "Nomor halaman PDF tidak tersedia")
    if extension == ".pdf" and proc.returncode != 0:
        raise HTTPException(422, "PDF tidak dapat dibaca atau diproses; periksa sandi dan coba pecah PDF menjadi berkas lebih kecil")
    if proc.returncode != 0:
        raise HTTPException(422, "Berkas tidak dapat dikonversi")
    return proc.stdout


def convert(data: bytes, extension: str) -> str:
    output = run_worker(data, extension)
    if len(output) > MAX_OUTPUT_BYTES:
        raise HTTPException(413, "Hasil konversi terlalu besar")
    return output.decode("utf-8")


def convert_pdf(data: bytes) -> tuple[str, str]:
    output = run_worker(data, ".pdf", "pdf-convert")
    try:
        payload = json.loads(output)
        markdown, engine = payload["markdown"], payload["engine"]
        if not isinstance(markdown, str) or engine != "markitdown":
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise HTTPException(502, "Hasil konversi PDF tidak valid") from None
    if len(markdown.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise HTTPException(413, "Hasil konversi terlalu besar")
    return markdown, engine


def vision_provider() -> tuple[str, str, str]:
    key = os.environ.get("SUMOPOD_API_KEY", "").strip()
    if key:
        return SUMOPOD_URL, key, "SumoPod"
    raise HTTPException(503, "Atur SUMOPOD_API_KEY di Render untuk OCR melalui SumoPod")


def request_vision(image: bytes, mime_type: str, prompt: str, max_tokens: int = 8192, timeout: int = 80, thinking: bool = False, route: str = "chat", minimal: bool = False) -> str:
    url, key, provider = vision_provider()
    if len(image) > MAX_BYTES:
        raise HTTPException(413, "Gambar terlalu besar untuk OCR AI")
    image_url = f"data:{mime_type};base64," + base64.b64encode(image).decode("ascii")
    if route == "responses":
        url = SUMOPOD_RESPONSES_URL
        payload = {
            "model": SUMOPOD_MODEL,
            "input": [{"role": "user", "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": image_url, "detail": "original"},
            ]}],
            "max_output_tokens": max_tokens,
            "reasoning": {"effort": "low" if thinking else "none"},
        }
    else:
        payload = {
            "model": SUMOPOD_MODEL,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_url, "detail": "original"}},
            ]}],
            "thinking": {"type": "enabled" if thinking else "disabled"},
            "max_tokens": max_tokens,
        }
        if thinking:
            payload["reasoning_effort"] = "low"
    if minimal:
        if route == "responses":
            payload.pop("reasoning", None)
            payload["input"][0]["content"][1].pop("detail", None)
        else:
            payload.pop("thinking", None)
            payload["messages"][0]["content"][1]["image_url"].pop("detail", None)
    try:
        result = httpx.post(
            url, json=payload,
            headers={"Authorization": "Bearer " + key},
            timeout=timeout, follow_redirects=False,
        )
    except httpx.TimeoutException:
        raise HTTPException(504, f"SumoPod melewati batas waktu respons ({timeout} detik)") from None
    except httpx.HTTPError:
        raise HTTPException(502, f"{provider} tidak dapat dihubungi") from None
    if result.status_code in {401, 403}:
        raise HTTPException(503, f"Kunci {provider} tidak valid atau tidak diizinkan")
    if result.status_code == 402:
        raise HTTPException(402, "SumoPod menolak OCR (HTTP 402: pembayaran atau kredit diperlukan). "
                            "Periksa status akun dan tagihan SumoPod.")
    if result.status_code == 429:
        raise HTTPException(429, f"Batas pemakaian {provider} tercapai; coba lagi nanti",
                            headers={"Retry-After": "60"})
    if result.status_code != 200:
        raise HTTPException(502, f"{provider} mengembalikan HTTP {result.status_code} pada {route}")
    if len(result.content) > MAX_OUTPUT_BYTES + 65536:
        raise HTTPException(502, f"Jawaban {provider} terlalu besar")
    try:
        reply = result.json()
        if route == "responses":
            content = reply.get("output_text")
            if not isinstance(content, str):
                content = "\n".join(
                    part["text"] for item in reply["output"]
                    for part in item.get("content", [])
                    if part.get("type") == "output_text" and isinstance(part.get("text"), str)
                )
            if reply.get("status") == "incomplete":
                raise HTTPException(502, "Transkripsi DeepSeek terpotong; coba lagi pada halaman ini")
        else:
            choice = reply["choices"][0]
            content = choice["message"]["content"]
            if choice.get("finish_reason") == "length":
                reasoning = choice["message"].get("reasoning_content") or ""
                usage = reply.get("usage") or {}
                logging.getLogger(__name__).warning(
                    "SumoPod truncated OCR: model=%r content_chars=%d reasoning_chars=%d completion_tokens=%r",
                    reply.get("model"), len(content or ""), len(reasoning), usage.get("completion_tokens"))
                raise HTTPException(502, "Transkripsi DeepSeek terpotong; coba lagi pada halaman ini")
        if not isinstance(content, str):
            raise ValueError
    except (KeyError, IndexError, TypeError, ValueError):
        raise HTTPException(502, f"Jawaban {provider} tidak valid pada {route}") from None
    return content.strip()


def verify_vision() -> None:
    """Choose only a SumoPod payload and route that read a fresh image challenge."""
    global vision_probe_ok, vision_probe_retry_at, vision_route, vision_minimal
    if vision_probe_ok:
        return
    now = time.monotonic()
    if now < vision_probe_retry_at:
        raise HTTPException(503, "SumoPod belum dapat membaca gambar; coba lagi nanti")
    from PIL import Image, ImageDraw, ImageFont
    code = secrets.choice(("KUCING", "JENDELA", "MERAH", "BIRU"))
    with Image.new("RGB", (600, 300), "white") as sample:
        draw = ImageDraw.Draw(sample)
        draw.text((55, 95), code, fill="black", font=ImageFont.load_default(size=72))
        buffer = BytesIO()
        sample.save(buffer, format="PNG")
    failures = []
    image_missing_reply = False
    for minimal in (False, True):
        for route in ("chat", "responses"):
            try:
                answer = request_vision(buffer.getvalue(), "image/png",
                                        "Baca kata besar pada gambar. Balas hanya kata itu.",
                                        max_tokens=4096, thinking=False, route=route,
                                        minimal=minimal)
            except HTTPException as exc:
                if exc.status_code in {402, 429} or (exc.status_code == 503 and "Kunci" in str(exc.detail)):
                    raise
                failures.append(str(exc.detail))
                logging.getLogger(__name__).warning(
                    "SumoPod vision probe route=%s minimal=%s failed: %s", route, minimal, exc.detail)
                continue
            if code in re.sub(r"[^A-Za-z0-9]", "", answer).upper():
                vision_route = route
                vision_minimal = minimal
                vision_probe_ok = True
                logging.getLogger(__name__).info(
                    "SumoPod vision route verified: route=%s minimal=%s", route, minimal)
                return
            failures.append("jawaban tidak cocok")
            if re.search(r"tidak ada gambar|gambar tidak ada|no image|image not provided",
                         answer, re.IGNORECASE):
                image_missing_reply = True
            logging.getLogger(__name__).warning(
                "SumoPod vision probe mismatch: route=%s minimal=%s expected=%s response=%r",
                route, minimal, code, answer[:160])
    vision_probe_retry_at = now + 60
    if image_missing_reply:
        raise HTTPException(503, "Rute SumoPod menyatakan gambar tidak tersedia meskipun aplikasi mengirim PNG base64. "
                            "Periksa penerusan input gambar pada SumoPod.")
    if failures and all("terpotong" in reason for reason in failures):
        raise HTTPException(503, "SumoPod memotong jawaban gambar uji pada kedua rute. "
                            "Model menghabiskan keluaran sebelum teks OCR tersedia.")
    raise HTTPException(503, "Jawaban SumoPod tidak cocok dengan gambar uji pada kedua rute, "
                        "termasuk payload gambar minimal. Periksa dukungan input gambar model pada SumoPod.")


def analyze_deepseek(image: bytes, mime_type: str, *, allow_empty: bool = False, locate: bool = False) -> dict:
    verify_vision()
    content = request_vision(image, mime_type, OCR_PROMPT, route=vision_route, minimal=vision_minimal)
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content).strip()
    try:
        parsed = json.loads(content)
    except (ValueError, TypeError):
        parsed = {"markdown": content, "regions": []}
    if not isinstance(parsed, dict) or not isinstance(parsed.get("markdown"), str):
        raise HTTPException(502, "Jawaban OCR DeepSeek tidak valid")
    markdown = parsed["markdown"].strip()
    if not markdown and not allow_empty:
        raise HTTPException(502, "DeepSeek mengembalikan hasil OCR kosong; periksa dukungan vision di SumoPod")
    output = markdown.encode("utf-8")
    if len(output) > MAX_OUTPUT_BYTES:
        raise HTTPException(413, "Hasil konversi terlalu besar")
    if locate and markdown and not parsed.get("regions"):
        try:
            region_content = request_vision(image, mime_type, REGIONS_PROMPT,
                                            max_tokens=4096, timeout=25, route=vision_route, minimal=vision_minimal).strip()
            if region_content.startswith("```"):
                region_content = re.sub(r"^```(?:json)?\s*|\s*```$", "", region_content).strip()
            region_payload = json.loads(region_content)
            if isinstance(region_payload, dict):
                parsed["regions"] = region_payload.get("regions", [])
        except (HTTPException, ValueError, TypeError):
            # A location failure must not discard an already valid transcription.
            pass
    regions = []
    for item in parsed.get("regions", []) if isinstance(parsed.get("regions"), list) else []:
        if len(regions) >= 600:
            break
        if not isinstance(item, dict):
            continue
        try:
            x, y, w, h = (float(item[k]) for k in ("x", "y", "w", "h"))
            line = item["text"]
        except (KeyError, TypeError, ValueError):
            continue
        if (isinstance(line, str) and line.strip() and len(line) <= 300 and
            all(math.isfinite(v) for v in (x, y, w, h)) and
            0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 - x and 0 < h <= 1 - y):
            regions.append({"x": x, "y": y, "w": w, "h": h,
                            "text": line.strip(), "source": "deepseek"})
    return {"markdown": markdown, "regions": regions}


def convert_sumopod(data: bytes, extension: str) -> str:
    # Decode untrusted images in the constrained worker; the provider key stays in this process.
    png = run_worker(data, extension, "prepare-vision", timeout=20)
    return analyze_deepseek(png, "image/png")["markdown"]


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/capabilities")
async def capabilities():
    try:
        _, _, provider = vision_provider()
    except HTTPException:
        provider = "unavailable"
    return {"image_ocr": provider.lower(), "image_ocr_model": SUMOPOD_MODEL}


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(ROOT / "static" / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/static/{filename}", include_in_schema=False)
async def static(filename: str):
    if filename not in {"app.js", "style.css", "zip.js"}:
        raise HTTPException(404)
    return FileResponse(ROOT / "static" / filename, headers={"Cache-Control": "no-cache"})


@app.post("/api/regions")
async def image_regions(request: Request):
    """Ask the configured vision model for text line locations."""
    _, extension = validate_filename(request.headers.get("x-filename"))
    if extension not in IMAGE_FORMATS:
        raise HTTPException(415, "Pratinjau lokasi teks hanya tersedia untuk gambar")
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > MAX_BYTES:
        raise HTTPException(413, "Ukuran berkas melebihi 10 MB")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_BYTES:
            raise HTTPException(413, "Ukuran berkas melebihi 10 MB")
        data.extend(chunk)
    validate_content(data, extension)
    vision_provider()
    async with quota_lock:
        now = time.monotonic()
        while region_requests and region_requests[0] <= now - 60:
            region_requests.popleft()
        if len(region_requests) >= 12:
            delay = max(1, math.ceil(60 - (now - region_requests[0])))
            raise HTTPException(429, f"Batas pratinjau tercapai; coba lagi dalam {delay} detik", headers={"Retry-After": str(delay)})
        if slots.locked():
            raise HTTPException(429, "Server sedang memproses berkas lain; coba lagi sebentar", headers={"Retry-After": "3"})
        region_requests.append(now)
    async with slots:
        png = await run_in_threadpool(run_worker, bytes(data), extension, "prepare-vision", timeout=20)
        result = await run_in_threadpool(analyze_deepseek, png, "image/png", allow_empty=True, locate=True)
    return JSONResponse({"engine": SUMOPOD_MODEL, "regions": result["regions"]},
                        headers={"Cache-Control": "no-store"})


@app.post("/api/pdf-preview")
async def pdf_preview(request: Request, page: int = 1, preview_only: bool = False):
    """Render a PDF page and transcribe its image with the configured vision model."""
    _, extension = validate_filename(request.headers.get("x-filename"))
    if extension != ".pdf":
        raise HTTPException(415, "Pratinjau halaman hanya tersedia untuk PDF")
    if page < 1 or page > 30:
        raise HTTPException(400, "Nomor halaman PDF tidak tersedia")
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > MAX_BYTES:
        raise HTTPException(413, "Ukuran berkas melebihi 10 MB")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_BYTES:
            raise HTTPException(413, "Ukuran berkas melebihi 10 MB")
        data.extend(chunk)
    validate_content(data, extension)
    if not preview_only:
        vision_provider()
    async with quota_lock:
        if not preview_only:
            now = time.monotonic()
            while region_requests and region_requests[0] <= now - 60:
                region_requests.popleft()
            if len(region_requests) >= 12:
                delay = max(1, math.ceil(60 - (now - region_requests[0])))
                raise HTTPException(429, f"Batas pratinjau tercapai; coba lagi dalam {delay} detik", headers={"Retry-After": str(delay)})
        if slots.locked():
            raise HTTPException(429, "Server sedang memproses berkas lain; coba lagi sebentar", headers={"Retry-After": "3"})
        if not preview_only:
            region_requests.append(now)
    async with slots:
        output = await run_in_threadpool(run_worker, bytes(data), extension, f"pdf-preview:{page}")
        if len(output) > MAX_OUTPUT_BYTES:
            raise HTTPException(413, "Pratinjau PDF terlalu besar")
        try:
            payload = json.loads(output)
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(502, "Pratinjau PDF tidak valid") from None
        if payload.get("image") and not preview_only:
            try:
                jpeg = base64.b64decode(payload["image"].split(",", 1)[1], validate=True)
            except (ValueError, IndexError):
                raise HTTPException(502, "Gambar halaman PDF tidak valid") from None
            try:
                analysis = await run_in_threadpool(analyze_deepseek, jpeg, "image/jpeg", locate=True)
            except HTTPException as exc:
                if exc.status_code not in {402, 502, 503, 504}:
                    raise
                payload["ocr_error"] = exc.detail
                payload["ocr_status"] = exc.status_code
            else:
                from app.worker import merge_regions
                payload["page_text"] = analysis["markdown"]
                payload["regions"] = merge_regions(payload["regions"], analysis["regions"])
                payload["page_source"] = "deepseek"
                payload["engine"] = SUMOPOD_MODEL
        if preview_only:
            payload["preview_only"] = True
        if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise HTTPException(413, "Pratinjau PDF terlalu besar")
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


@app.post("/api/convert")
async def convert_file(request: Request):
    name, extension = validate_filename(request.headers.get("x-filename"))
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > MAX_BYTES:
        raise HTTPException(413, "Ukuran berkas melebihi 10 MB")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_BYTES:
            raise HTTPException(413, "Ukuran berkas melebihi 10 MB")
        data.extend(chunk)
    validate_content(data, extension)
    if extension in IMAGE_FORMATS or extension == ".pdf":
        vision_provider()
    # Bound simultaneous traffic; the browser waits and retries a temporary 429.
    async with quota_lock:
        now = time.monotonic()
        while recent_requests and recent_requests[0] <= now - 60:
            recent_requests.popleft()
        if len(recent_requests) >= MAX_REQUESTS_PER_MINUTE:
            delay = max(1, math.ceil(60 - (now - recent_requests[0])))
            raise HTTPException(429, "Server sedang mengatur antrean; coba lagi sebentar",
                                headers={"Retry-After": str(delay)})
        if slots.locked():
            raise HTTPException(429, "Server sibuk; coba lagi sebentar", headers={"Retry-After": "3"})
        recent_requests.append(now)
    async with slots:
        if extension in IMAGE_FORMATS:
            markdown = await run_in_threadpool(convert_sumopod, bytes(data), extension)
            engine = SUMOPOD_MODEL
        elif extension == ".pdf":
            markdown, engine = await run_in_threadpool(convert_pdf, bytes(data))
        else:
            markdown = await run_in_threadpool(convert, bytes(data), extension)
            engine = "markitdown"
    safe_stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(name).stem)[:80] or "document"
    return Response(
        markdown,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_stem}.md"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-OCR-Engine": engine,
        },
    )


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers={**(exc.headers or {}), "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )
