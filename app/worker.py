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
MAX_PDF_PAGES = 30


def prepare_image(data: bytes, extension: str) -> tuple[int, bytes]:
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
                        rgb.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
                        image_bytes = io.BytesIO()
                        rgb.save(image_bytes, format="PNG")
    except Image.DecompressionBombError:
        return 4, b""
    except (UnidentifiedImageError, OSError, ValueError):
        return 5, b""

    return 0, image_bytes.getvalue()


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


def read_pdf_page_text(data: bytes, page: int) -> str:
    """Return selectable text from one page even when image OCR is unavailable."""
    try:
        result = subprocess.run(
            ["pdftotext", "-f", str(page), "-l", str(page), "-layout", "-enc", "UTF-8", "-", "-"],
            input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            check=False, timeout=5,
        )
        if result.returncode or len(result.stdout) > 2 * 1024 * 1024:
            return ""
        return result.stdout.decode("utf-8", errors="replace").strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


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


def order_ocr_words(words: list[dict]) -> str:
    """Assemble OCR from page coordinates, including rows split across TSV blocks."""
    if not words:
        return ""
    lines = {}
    for word in words:
        lines.setdefault(word["line"], []).append(word)
    fragments = []
    for line_words in lines.values():
        line_words.sort(key=lambda word: word["x"])
        top = min(word["y"] for word in line_words)
        bottom = max(word["y"] + word["h"] for word in line_words)
        fragments.append({"x": line_words[0]["x"], "right": max(word["x"] + word["w"] for word in line_words),
                          "top": top, "bottom": bottom, "center": (top + bottom) / 2,
                          "text": " ".join(word["text"] for word in line_words)})
    fragments.sort(key=lambda fragment: (fragment["center"], fragment["x"]))
    rows = []
    for fragment in fragments:
        if rows and abs(fragment["center"] - rows[-1]["center"]) <= max(
                6, min(fragment["bottom"] - fragment["top"], rows[-1]["bottom"] - rows[-1]["top"]) * .45):
            row = rows[-1]
            row["fragments"].append(fragment)
            row["top"] = min(row["top"], fragment["top"])
            row["bottom"] = max(row["bottom"], fragment["bottom"])
        else:
            rows.append({"center": fragment["center"], "top": fragment["top"],
                         "bottom": fragment["bottom"], "fragments": [fragment]})
    heights = sorted(row["bottom"] - row["top"] for row in rows)
    typical_height = heights[len(heights) // 2]
    output = []
    previous_bottom = None
    for row in rows:
        fragments = sorted(row["fragments"], key=lambda fragment: fragment["x"])
        pieces = []
        last_right = None
        for fragment in fragments:
            if pieces:
                pieces.append(" | " if fragment["x"] - last_right > max(18, typical_height * 1.5) else " ")
            pieces.append(fragment["text"])
            last_right = max(last_right or 0, fragment["right"])
        if output:
            output.append("\n\n" if row["top"] - previous_bottom > typical_height * 1.5 else "\n")
        output.append("".join(pieces))
        previous_bottom = row["bottom"]
    return "".join(output)


def ocr_rendered_page(jpeg: bytes) -> tuple[str, list[dict]]:
    """Read text and word locations in one Tesseract pass over the page image."""
    from PIL import Image

    with Image.open(io.BytesIO(jpeg)) as image:
        width, height = image.size
    result = subprocess.run(
        ["tesseract", "stdin", "stdout", "-l", "ind+eng", "--psm", "3", "tsv"],
        input=jpeg, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        check=False, timeout=55,
    )
    if result.returncode or len(result.stdout) > 2 * 1024 * 1024:
        raise ValueError("Page image OCR failed")
    words = []
    regions = []
    for row in csv.DictReader(io.StringIO(result.stdout.decode("utf-8", errors="replace")),
                              delimiter="\t", quoting=csv.QUOTE_NONE):
        try:
            word = row["text"].strip()
            x, y, w, h = (int(row[key]) for key in ("left", "top", "width", "height"))
            confidence = float(row["conf"])
            line = (row["page_num"], row["block_num"], row["par_num"], row["line_num"])
        except (KeyError, TypeError, ValueError):
            continue
        if not word:
            continue
        if 0 <= x < width and 0 <= y < height and 0 < w <= width-x and 0 < h <= height-y:
            words.append({"line": line, "x": x, "y": y, "w": w, "h": h, "text": word})
        if confidence >= 35 and 0 <= x < width and 0 <= y < height and 0 < w <= width-x and 0 < h <= height-y and len(regions) < 600:
            regions.append({"x": x/width, "y": y/height, "w": w/width, "h": h/height,
                            "text": word[:300], "source": "tesseract"})
    return order_ocr_words(words), regions


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
    payload = {"engine": "tesseract", "page": page, "total_pages": pages,
               "image": None, "regions": [], "page_text": "", "page_source": None}
    try:
        jpeg = render_pdf_page(data, page, zoom=zoom)
        payload["image"] = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
        payload["page_text"], payload["regions"] = ocr_rendered_page(jpeg)
        payload["page_source"] = "tesseract" if payload["page_text"] else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        payload["warning"] = "Gambar halaman atau OCR lokal gagal. Coba lagi pada halaman ini."
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
        data = sys.stdin.buffer.read(10 * 1024 * 1024 + 1)
        if extension in IMAGE_EXTENSIONS:
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
