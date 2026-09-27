"""Short-lived conversion process: accepts only raw file bytes on stdin."""

import io
import base64
import csv
import json
import re
import socket
import subprocess
import sys
import xml.etree.ElementTree as ET

IMAGE_EXTENSIONS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}
MAX_IMAGE_PIXELS = 8_000_000
MAX_SCANNED_PDF_PAGES = 8


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


def merge_regions(existing: list[dict], additions: list[dict], limit: int = 600) -> list[dict]:
    """Keep new text locations while removing near-identical OCR detections."""
    regions = list(existing[:limit])
    for region in additions:
        if len(regions) >= limit:
            break
        area = region["w"] * region["h"]
        if area <= 0:
            continue
        candidate_text = re.sub(r"\W+", "", region["text"].casefold())
        duplicate = False
        for other in regions:
            overlap_w = max(0, min(region["x"] + region["w"], other["x"] + other["w"]) - max(region["x"], other["x"]))
            overlap_h = max(0, min(region["y"] + region["h"], other["y"] + other["h"]) - max(region["y"], other["y"]))
            if overlap_w * overlap_h / area < 0.65:
                continue
            other_text = re.sub(r"\W+", "", other["text"].casefold())
            if other.get("source") == "pdf-text" or (candidate_text and other_text and
                (candidate_text in other_text or other_text in candidate_text)):
                duplicate = True
                break
        if not duplicate:
            regions.append(region)
    return regions


def read_image_regions(data: bytes, extension: str) -> tuple[int, bytes]:
    """Return local OCR line positions, including extra detections from sparse text."""
    from PIL import Image

    # Keep the screenshot's full resolution so small UI text remains detectable.
    code, png = prepare_image(data, extension)
    if code:
        return code, b""
    with Image.open(io.BytesIO(png)) as image:
        width, height = image.size
    regions = []
    had_error = False
    for psm in ("3", "11"):
        try:
            result = subprocess.run(
                ["tesseract", "stdin", "stdout", "-l", "ind+eng", "--psm", psm, "tsv"],
                input=png, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                check=False, timeout=7,
            )
        except (OSError, subprocess.TimeoutExpired):
            had_error = True
            continue
        if result.returncode != 0:
            had_error = True
            continue
        lines = {}
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
            had_error = True
            continue
        additions = [{
            "x": round(line["x"] / width, 5),
            "y": round(line["y"] / height, 5),
            "w": round((line["right"] - line["x"]) / width, 5),
            "h": round((line["bottom"] - line["y"]) / height, 5),
            "text": " ".join(line["words"])[:300],
            "source": "tesseract",
        } for line in list(lines.values())[:600]]
        regions = merge_regions(regions, additions)
    if had_error and not regions:
        return 2, b""
    return 0, json.dumps({"engine": "tesseract", "regions": regions}, ensure_ascii=False).encode("utf-8")


def read_pdf_text_regions(data: bytes, page: int) -> list[dict]:
    """Read boxes from a PDF text layer, including individual table cells."""
    try:
        result = subprocess.run(
            ["pdftotext", "-f", str(page), "-l", str(page), "-bbox-layout", "-", "-"],
            input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            check=False, timeout=5,
        )
        if result.returncode or len(result.stdout) > 2 * 1024 * 1024:
            return []
        root = ET.fromstring(result.stdout)
        page_node = next((node for node in root.iter() if node.tag.endswith("page")), None)
        if page_node is None:
            return []
        width, height = float(page_node.attrib["width"]), float(page_node.attrib["height"])
        if not (0 < width < 100000 and 0 < height < 100000):
            return []
        regions = []
        for line in page_node.iter():
            if not line.tag.endswith("line"):
                continue
            words = [word.text.strip() for word in line if word.tag.endswith("word") and word.text and word.text.strip()]
            if not words:
                continue
            x1, y1, x2, y2 = (float(line.attrib[key]) for key in ("xMin", "yMin", "xMax", "yMax"))
            if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
                continue
            regions.append({
                "x": round(x1 / width, 5), "y": round(y1 / height, 5),
                "w": round((x2 - x1) / width, 5), "h": round((y2 - y1) / height, 5),
                "text": " ".join(words)[:300], "source": "pdf-text",
            })
            if len(regions) >= 600:
                break
        return regions
    except (OSError, ValueError, KeyError, ET.ParseError, subprocess.TimeoutExpired):
        return []


def pdf_pages(data: bytes) -> int:
    result = subprocess.run(
        ["pdfinfo", "-"], input=data, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, check=False, timeout=5,
    )
    match = re.search(rb"^Pages:\s*(\d+)\s*$", result.stdout, re.MULTILINE)
    if result.returncode or not match:
        raise ValueError("Invalid PDF")
    return int(match.group(1))


def render_pdf_page(data: bytes, page: int) -> bytes:
    result = subprocess.run(
        ["pdftoppm", "-f", str(page), "-l", str(page), "-scale-to", "1600",
         "-singlefile", "-jpeg", "-"],
        input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        check=False, timeout=9,
    )
    if result.returncode or not result.stdout.startswith(b"\xff\xd8\xff"):
        raise ValueError("PDF page could not be rendered")
    return result.stdout


def read_pdf_text(data: bytes) -> tuple[int, bytes]:
    from markitdown import MarkItDown

    try:
        result = MarkItDown(enable_plugins=False).convert_stream(io.BytesIO(data), file_extension=".pdf")
        markdown = (result.markdown or "").strip()
    except Exception:
        # A readable scan may still confuse the PDF text parser. Try local OCR.
        markdown = ""
    engine = "markitdown"
    if not markdown:
        pages = pdf_pages(data)
        if pages > MAX_SCANNED_PDF_PAGES:
            return 6, b""
        extracted = []
        successful_pages = 0
        for page in range(1, pages + 1):
            try:
                code, output = read_image_text(render_pdf_page(data, page), ".jpg")
            except (OSError, ValueError, subprocess.TimeoutExpired):
                code, output = 2, b""
            if code == 3:
                return code, b""
            if code:
                extracted.append(f"## Halaman {page}\n\n> OCR gagal membaca halaman ini. Coba ekspor halaman sebagai gambar.")
                continue
            successful_pages += 1
            extracted.append(f"## Halaman {page}\n\n{output.decode('utf-8').strip()}")
        if not successful_pages:
            return 2, b""
        markdown = "\n\n".join(extracted).strip()
        engine = "tesseract-pdf-partial" if successful_pages < pages else "tesseract-pdf"
    return 0, json.dumps({"markdown": markdown, "engine": engine}, ensure_ascii=False).encode("utf-8")


def read_pdf_preview(data: bytes, page: int) -> tuple[int, bytes]:
    pages = pdf_pages(data)
    if page < 1 or page > pages:
        return 7, b""
    payload = {"engine": "tesseract", "page": page, "total_pages": pages,
               "image": None, "regions": []}
    text_regions = read_pdf_text_regions(data, page)
    try:
        jpeg = render_pdf_page(data, page)
        payload["image"] = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
        code, output = read_image_regions(jpeg, ".jpg")
        if code:
            payload["regions"] = text_regions
            payload["warning"] = "Halaman ditampilkan, tetapi OCR lokal tidak berhasil memetakan teks pada gambar."
        else:
            payload["regions"] = merge_regions(text_regions, json.loads(output)["regions"])
            if not payload["regions"]:
                payload["warning"] = "Teks PDF dan OCR lokal tidak menemukan kotak pada halaman ini. Coba lagi atau unggah halaman sebagai gambar."
        if text_regions:
            payload["engine"] = "pdf-text+tesseract"
    except (OSError, ValueError, subprocess.TimeoutExpired):
        payload["warning"] = "Halaman ini tidak dapat dirender. Coba lagi atau lanjut ke halaman berikutnya."
    return 0, json.dumps(payload, ensure_ascii=False).encode("utf-8")


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
        elif extension == ".pdf" and len(sys.argv) > 2 and sys.argv[2] == "pdf-convert":
            code, output = read_pdf_text(data)
            if code:
                return code
        elif extension == ".pdf" and len(sys.argv) > 2 and sys.argv[2].startswith("pdf-preview:"):
            code, output = read_pdf_preview(data, int(sys.argv[2].split(":", 1)[1]))
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
