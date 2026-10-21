"""Short-lived conversion process: accepts only raw file bytes on stdin."""

import io
import base64
import json
import re
import socket
import subprocess
import sys

IMAGE_EXTENSIONS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP",
                    ".tif": "TIFF", ".tiff": "TIFF"}
MAX_IMAGE_PIXELS = 8_000_000
MAX_PDF_PAGES = 30


def prepare_image(data: bytes, extension: str) -> tuple[int, bytes]:
    from PIL import Image, ImageOps, UnidentifiedImageError

    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != IMAGE_EXTENSIONS[extension]:
                return 5, b""
            if getattr(image, "n_frames", 1) != 1:
                return (10 if image.format == "TIFF" else 5), b""
            if image.width * image.height > MAX_IMAGE_PIXELS:
                return 4, b""
            # Normalize orientation, flatten transparency, and strip metadata.
            with ImageOps.exif_transpose(image) as oriented:
                with oriented.convert("RGBA") as rgba:
                    with Image.new("RGB", rgba.size, "white") as rgb:
                        rgb.paste(rgba, mask=rgba.getchannel("A"))
                        rgb.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
                        image_bytes = io.BytesIO()
                        rgb.save(image_bytes, format="PNG")
    except Image.DecompressionBombError:
        return 4, b""
    except (UnidentifiedImageError, OSError, ValueError):
        return 5, b""

    return 0, image_bytes.getvalue()


def pdf_pages(data: bytes) -> int:
    result = subprocess.run(
        ["pdfinfo", "-"], input=data, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, check=False, timeout=5,
    )
    match = re.search(rb"^Pages:\s*(\d+)\s*$", result.stdout, re.MULTILINE)
    if result.returncode or not match:
        raise ValueError("Invalid PDF")
    return int(match.group(1))


def render_pdf_page(data: bytes, page: int, zoom: bool = False) -> bytes:
    result = subprocess.run(
        ["pdftoppm", "-f", str(page), "-l", str(page), "-scale-to", "3200" if zoom else "2800",
         "-singlefile", "-jpeg", "-jpegopt", "quality=82", "-"],
        input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        check=False, timeout=15,
    )
    if result.returncode or not result.stdout.startswith(b"\xff\xd8\xff"):
        raise ValueError("PDF page could not be rendered")
    return result.stdout


def read_pdf_text(data: bytes) -> tuple[int, bytes]:
    # Only validate the PDF here. The browser transcribes full-page raster images.
    pages = pdf_pages(data)
    if pages > MAX_PDF_PAGES:
        return 8, b""
    return 0, b'{"markdown":"","engine":"pdf-images"}'


def read_pdf_preview(data: bytes, page: int, zoom: bool = False) -> tuple[int, bytes]:
    pages = pdf_pages(data)
    if pages > MAX_PDF_PAGES:
        return 8, b""
    if page < 1 or page > pages:
        return 7, b""
    payload = {"engine": "baidu/Unlimited-OCR", "page": page, "total_pages": pages,
               "image": None, "regions": [], "page_text": "", "page_source": None}
    try:
        jpeg = render_pdf_page(data, page, zoom=zoom)
        payload["image"] = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        payload["warning"] = "Gambar halaman gagal dirender. Coba lagi pada halaman ini."
    return 0, json.dumps(payload, ensure_ascii=False).encode("utf-8")


def block_network(*args, **kwargs):
    raise OSError("Network disabled during document conversion")


def main() -> int:
    # Defense in depth; run the container without privileged mounts too.
    if sys.platform != "win32":
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (75, 75))
        resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
    socket.socket.connect = block_network
    socket.socket.connect_ex = block_network
    socket.create_connection = block_network
    extension = sys.argv[1]
    try:
        mode = sys.argv[2] if len(sys.argv) > 2 else ""
        input_limit_mb = 20 if mode.startswith("tool-merge:") else 11 if mode.startswith("tool-sign:") else 10
        data = sys.stdin.buffer.read(input_limit_mb * 1024 * 1024 + 1)
        if mode.startswith("tool-"):
            from app.document_tools import ToolError, execute
            try:
                output = execute(data, mode)
            except ToolError as exc:
                sys.stderr.write(json.dumps({"status": exc.status, "message": exc.message}))
                return 9
        elif extension in IMAGE_EXTENSIONS:
            code, output = prepare_image(data, extension)
            if code:
                return code
        elif extension == ".pdf" and len(sys.argv) > 2 and sys.argv[2] == "pdf-convert":
            code, output = read_pdf_text(data)
            if code:
                return code
        elif extension == ".pdf" and len(sys.argv) > 2 and sys.argv[2].startswith("pdf-preview:"):
            parts = sys.argv[2].split(":")
            code, output = read_pdf_preview(data, int(parts[1]), zoom=len(parts) == 3 and parts[2] == "zoom")
            if code:
                return code
        else:
            from markitdown import MarkItDown

            result = MarkItDown(enable_plugins=False).convert_stream(
                io.BytesIO(data), file_extension=extension,
            )
            output = (result.markdown or "").encode("utf-8")
        output_limit = 15 * 1024 * 1024 + 4096 if mode.startswith("tool-") else 10 * 1024 * 1024 if mode == "prepare-vision" else 2 * 1024 * 1024
        if len(output) > output_limit:
            return 3
        sys.stdout.buffer.write(output)
        return 0
    except Exception:
        # Untrusted converter exceptions can include file contents; don't log them.
        return 2


if __name__ == "__main__":
    sys.exit(main())
