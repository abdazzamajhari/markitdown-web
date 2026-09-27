"""Authenticated, bounded upload API and browser interface."""

import asyncio
import hmac
import os
import re
import subprocess
import sys
import zipfile
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool

ROOT = Path(__file__).resolve().parent
ALLOWED = {".pdf", ".docx", ".pptx", ".xlsx", ".txt", ".csv", ".json"}
OFFICE_MARKERS = {
    ".docx": "word/document.xml",
    ".pptx": "ppt/presentation.xml",
    ".xlsx": "xl/workbook.xml",
}
MAX_BYTES = 10 * 1024 * 1024
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_UNZIPPED_BYTES = 40 * 1024 * 1024
TIMEOUT_SECONDS = 30

app = FastAPI(title="MarkItDown Web", version="1.0.0", docs_url=None, redoc_url=None)
slots = asyncio.Semaphore(1)


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


def convert(data: bytes, extension: str) -> str:
    # No shell, no user-controlled paths/URLs, no inherited credentials.
    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
           "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1",
           "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
           "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1"}
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "app.worker", extension],
            input=data, capture_output=True, timeout=TIMEOUT_SECONDS,
            check=False, env=env,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Konversi melebihi batas waktu") from None
    if proc.returncode == 3:
        raise HTTPException(413, "Hasil konversi terlalu besar")
    if proc.returncode != 0:
        raise HTTPException(422, "Berkas tidak dapat dikonversi")
    if len(proc.stdout) > MAX_OUTPUT_BYTES:
        raise HTTPException(413, "Hasil konversi terlalu besar")
    return proc.stdout.decode("utf-8")


@app.get("/health")
async def health():
    return {"status": "ok"}


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
    secret = os.environ.get("WEB_API_KEY", "")
    if not secret:
        raise HTTPException(503, "WEB_API_KEY belum diatur pada server")
    authorization = request.headers.get("authorization", "")
    if not hmac.compare_digest(authorization, "Bearer " + secret):
        raise HTTPException(401, "Kunci API tidak valid", headers={"WWW-Authenticate": "Bearer"})
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
    # Reject rather than queue unbounded conversion jobs.
    if slots.locked():
        raise HTTPException(429, "Server sibuk; coba lagi sebentar")
    async with slots:
        markdown = await run_in_threadpool(convert, bytes(data), extension)
    safe_stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(name).stem)[:80] or "document"
    return Response(
        markdown,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_stem}.md"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers={**(exc.headers or {}), "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )
