"""Short-lived conversion process: accepts only raw file bytes on stdin."""

import io
import socket
import subprocess
import sys

IMAGE_EXTENSIONS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}
MAX_IMAGE_PIXELS = 8_000_000


def read_image_text(data: bytes, extension: str) -> tuple[int, bytes]:
    from PIL import Image, ImageOps, UnidentifiedImageError

    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != IMAGE_EXTENSIONS[extension] or getattr(image, "n_frames", 1) != 1:
                return 5, b""
            if image.width * image.height > MAX_IMAGE_PIXELS:
                return 4, b""
            # Normalize orientation and strip metadata before passing the image to OCR.
            with ImageOps.exif_transpose(image) as oriented:
                with oriented.convert("RGB") as rgb:
                    image_bytes = io.BytesIO()
                    rgb.save(image_bytes, format="PNG")
    except Image.DecompressionBombError:
        return 4, b""
    except (UnidentifiedImageError, OSError, ValueError):
        return 5, b""

    try:
        result = subprocess.run(
            ["tesseract", "stdin", "stdout", "-l", "ind+eng", "--psm", "3"],
            input=image_bytes.getvalue(), stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, check=False, timeout=22,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 2, b""
    if result.returncode != 0:
        return 2, b""
    return (3 if len(result.stdout) > 2 * 1024 * 1024 else 0), result.stdout


def block_network(*args, **kwargs):
    raise OSError("Network disabled during document conversion")


def main() -> int:
    # Defense in depth; run the container without privileged mounts too.
    if sys.platform != "win32":
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (25, 25))
        resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
    socket.socket.connect = block_network
    socket.socket.connect_ex = block_network
    socket.create_connection = block_network
    extension = sys.argv[1]
    try:
        data = sys.stdin.buffer.read(10 * 1024 * 1024 + 1)
        if extension in IMAGE_EXTENSIONS:
            code, output = read_image_text(data, extension)
            if code:
                return code
        else:
            from markitdown import MarkItDown

            result = MarkItDown(enable_plugins=False).convert_stream(
                io.BytesIO(data), file_extension=extension,
            )
            output = (result.markdown or "").encode("utf-8")
        if len(output) > 2 * 1024 * 1024:
            return 3
        sys.stdout.buffer.write(output)
        return 0
    except Exception:
        # Untrusted converter exceptions can include file contents; don't log them.
        return 2


if __name__ == "__main__":
    sys.exit(main())
