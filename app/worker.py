"""Short-lived conversion process: accepts only raw file bytes on stdin."""

import io
import csv
import json
import socket
import subprocess
import sys

IMAGE_EXTENSIONS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}
MAX_IMAGE_PIXELS = 8_000_000


def prepare_image(data: bytes, extension: str, for_vision: bool = False) -> tuple[int, bytes]:
    from PIL import Image, ImageOps, UnidentifiedImageError

    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != IMAGE_EXTENSIONS[extension] or getattr(image, "n_frames", 1) != 1:
                return 5, b""
            if image.width * image.height > MAX_IMAGE_PIXELS:
                return 4, b""
            # Normalize orientation, flatten transparency, and strip metadata.
            with ImageOps.exif_transpose(image) as oriented:
                with oriented.convert("RGBA") as rgba:
                    with Image.new("RGB", rgba.size, "white") as rgb:
                        rgb.paste(rgba, mask=rgba.getchannel("A"))
                        if for_vision:
                            rgb.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
                        target = rgb
                        if not for_vision and max(rgb.size) < 1000:
                            scale = 1000 / max(rgb.size)
                            target = rgb.resize((round(rgb.width * scale), round(rgb.height * scale)), Image.Resampling.LANCZOS)
                        image_bytes = io.BytesIO()
                        try:
                            target.save(image_bytes, format="PNG")
                        finally:
                            if target is not rgb:
                                target.close()
    except Image.DecompressionBombError:
        return 4, b""
    except (UnidentifiedImageError, OSError, ValueError):
        return 5, b""

    return 0, image_bytes.getvalue()


def read_image_text(data: bytes, extension: str) -> tuple[int, bytes]:
    code, png = prepare_image(data, extension)
    if code:
        return code, b""
    for psm in ("3", "11"):
        try:
            result = subprocess.run(
                ["tesseract", "stdin", "stdout", "-l", "ind+eng", "--psm", psm],
                input=png, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, check=False, timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return 2, b""
        if result.returncode != 0:
            return 2, b""
        if result.stdout.strip():
            return (3 if len(result.stdout) > 2 * 1024 * 1024 else 0), result.stdout
    return 0, b""


def read_image_regions(data: bytes, extension: str) -> tuple[int, bytes]:
    """Return local OCR line positions for a visual guide, separate from AI output."""
    from PIL import Image

    # Keep the screenshot's full resolution so small UI text remains detectable.
    code, png = prepare_image(data, extension)
    if code:
        return code, b""
    with Image.open(io.BytesIO(png)) as image:
        width, height = image.size
    lines = {}
    for psm in ("3", "11"):
        try:
            result = subprocess.run(
                ["tesseract", "stdin", "stdout", "-l", "ind+eng", "--psm", psm, "tsv"],
                input=png, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                check=False, timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return 2, b""
        if result.returncode != 0:
            return 2, b""
        try:
            for row in csv.DictReader(io.StringIO(result.stdout.decode("utf-8", "replace")), delimiter="\t"):
                word = row["text"].strip()
                if row["level"] != "5" or not word or float(row["conf"]) < 0:
                    continue
                x, y, w, h = (int(row[k]) for k in ("left", "top", "width", "height"))
                if w <= 0 or h <= 0:
                    continue
                key = (row["page_num"], row["block_num"], row["par_num"], row["line_num"])
                if key not in lines:
                    lines[key] = {"x": x, "y": y, "right": x + w, "bottom": y + h, "words": []}
                line = lines[key]
                line["x"] = min(line["x"], x)
                line["y"] = min(line["y"], y)
                line["right"] = max(line["right"], x + w)
                line["bottom"] = max(line["bottom"], y + h)
                line["words"].append(word)
        except (KeyError, TypeError, ValueError):
            return 2, b""
        if lines:
            break
    regions = []
    for line in list(lines.values())[:600]:
        regions.append({
            "x": round(line["x"] / width, 5),
            "y": round(line["y"] / height, 5),
            "w": round((line["right"] - line["x"]) / width, 5),
            "h": round((line["bottom"] - line["y"]) / height, 5),
            "text": " ".join(line["words"])[:300],
        })
    return 0, json.dumps({"engine": "tesseract", "regions": regions}, ensure_ascii=False).encode("utf-8")


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
            if len(sys.argv) > 2 and sys.argv[2] == "prepare-vision":
                code, output = prepare_image(data, extension, for_vision=True)
            elif len(sys.argv) > 2 and sys.argv[2] == "regions":
                code, output = read_image_regions(data, extension)
            else:
                code, output = read_image_text(data, extension)
            if code:
                return code
        else:
            from markitdown import MarkItDown

            result = MarkItDown(enable_plugins=False).convert_stream(
                io.BytesIO(data), file_extension=extension,
            )
            output = (result.markdown or "").encode("utf-8")
        output_limit = 10 * 1024 * 1024 if len(sys.argv) > 2 and sys.argv[2] == "prepare-vision" else 2 * 1024 * 1024
        if len(output) > output_limit:
            return 3
        sys.stdout.buffer.write(output)
        return 0
    except Exception:
        # Untrusted converter exceptions can include file contents; don't log them.
        return 2


if __name__ == "__main__":
    sys.exit(main())
