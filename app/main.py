"""Public, bounded upload API and browser interface."""

import asyncio
import base64
import os
import re
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
MAX_REMOTE_REQUESTS_PER_HOUR = 30
OLMOCR_URL = "https://ai2endpoints.cirrascale.ai/api/chat/completions"
OLMOCR_MODEL = "olmOCR-2-7B-1025"
OLMOCR_PROMPT = (
    "Attached is one document image. Transcribe all readable text in natural order. "
    "Represent tables as HTML and equations as LaTeX. Do not invent text. "
    "Return Markdown with YAML front matter for primary_language, is_rotation_valid, "
    "rotation_correction, is_table, and is_diagram."
)

app = FastAPI(title="MarkItDown Web", version="1.0.0", docs_url=None, redoc_url=None)
slots = asyncio.Semaphore(1)
quota_lock = asyncio.Lock()
recent_requests: deque[float] = deque()
remote_requests: deque[float] = deque()


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
    if proc.returncode != 0:
        raise HTTPException(422, "Berkas tidak dapat dikonversi")
    return proc.stdout


def convert(data: bytes, extension: str) -> str:
    output = run_worker(data, extension)
    if len(output) > MAX_OUTPUT_BYTES:
        raise HTTPException(413, "Hasil konversi terlalu besar")
    return output.decode("utf-8")


def convert_olmocr(data: bytes, extension: str) -> str:
    # Decode untrusted images in the constrained worker; the provider key stays in this process.
    png = run_worker(data, extension, "prepare-olmocr", timeout=20)
    if len(png) > MAX_BYTES:
        raise HTTPException(413, "Gambar terlalu besar untuk olmOCR 2")
    payload = {
        "model": OLMOCR_MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": OLMOCR_PROMPT},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode("ascii")}},
        ]}],
        "temperature": 0.1,
        "max_tokens": 4096,
        "stream": False,
    }
    try:
        result = httpx.post(
            OLMOCR_URL, json=payload,
            headers={"Authorization": "Bearer " + os.environ["CIRRASCALE_API_KEY"]},
            timeout=80, follow_redirects=False,
        )
    except httpx.HTTPError:
        raise HTTPException(502, "olmOCR 2 tidak dapat dihubungi") from None
    if result.status_code in {401, 403}:
        raise HTTPException(503, "Kunci layanan olmOCR 2 tidak valid")
    if result.status_code == 429:
        raise HTTPException(503, "Kuota layanan olmOCR 2 tercapai; coba lagi nanti")
    if result.status_code != 200:
        raise HTTPException(502, f"Penyedia olmOCR 2 mengembalikan HTTP {result.status_code}")
    if len(result.content) > MAX_OUTPUT_BYTES + 65536:
        raise HTTPException(502, "Jawaban olmOCR 2 terlalu besar")
    try:
        markdown = result.json()["choices"][0]["message"]["content"]
        if not isinstance(markdown, str):
            raise ValueError
    except (KeyError, IndexError, TypeError, ValueError):
        raise HTTPException(502, "Jawaban olmOCR 2 tidak valid") from None
    # The model's front matter describes the image; only return its transcription.
    markdown = re.sub(r"\A\s*---\s*\n.*?\n---\s*\n", "", markdown, count=1, flags=re.DOTALL).strip()
    if not markdown:
        raise HTTPException(422, "olmOCR 2 tidak menemukan teks pada gambar")
    output = markdown.encode("utf-8")
    if len(output) > MAX_OUTPUT_BYTES:
        raise HTTPException(413, "Hasil konversi terlalu besar")
    return markdown


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/capabilities")
async def capabilities():
    return {"image_ocr": "olmocr2" if os.environ.get("CIRRASCALE_API_KEY") else "tesseract"}


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/static/{filename}", include_in_schema=False)
async def static(filename: str):
    if filename not in {"app.js", "style.css"}:
        raise HTTPException(404)
    return FileResponse(ROOT / "static" / filename)


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
    # Bound public traffic per instance and reject rather than queue conversions.
    async with quota_lock:
        now = time.monotonic()
        while recent_requests and recent_requests[0] <= now - 60:
            recent_requests.popleft()
        while remote_requests and remote_requests[0] <= now - 3600:
            remote_requests.popleft()
        use_olmocr = extension in IMAGE_FORMATS and bool(os.environ.get("CIRRASCALE_API_KEY"))
        if len(recent_requests) >= MAX_REQUESTS_PER_MINUTE:
            raise HTTPException(429, "Batas konversi sementara tercapai; coba lagi sebentar")
        if use_olmocr and len(remote_requests) >= MAX_REMOTE_REQUESTS_PER_HOUR:
            raise HTTPException(429, "Batas olmOCR 2 per jam tercapai; coba lagi nanti")
        if slots.locked():
            raise HTTPException(429, "Server sibuk; coba lagi sebentar")
        recent_requests.append(now)
        if use_olmocr:
            remote_requests.append(now)
    async with slots:
        if use_olmocr:
            markdown = await run_in_threadpool(convert_olmocr, bytes(data), extension)
        else:
            markdown = await run_in_threadpool(convert, bytes(data), extension)
    safe_stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(name).stem)[:80] or "document"
    return Response(
        markdown,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_stem}.md"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-OCR-Engine": "olmocr2" if use_olmocr else ("tesseract" if extension in IMAGE_FORMATS else "markitdown"),
        },
    )


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers={**(exc.headers or {}), "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )
