"""Offline document operations, executed only in the bounded worker."""
import base64
import io
import json
import math
import warnings

from PIL import Image, ImageOps

MAX_PIXELS = 24_000_000
MAX_RESULT = 15 * 1024 * 1024


class ToolError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def number(options, key, default, low, high, integer=False):
    value = options.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ToolError(400, "Pengaturan angka tidak valid")
    if not low <= value <= high or (integer and value != int(value)):
        raise ToolError(400, "Pengaturan berada di luar batas")
    return int(value) if integer else value


def flag(options, key, default=True):
    value = options.get(key, default)
    if not isinstance(value, bool):
        raise ToolError(400, "Pengaturan pilihan tidak valid")
    return value


def read_image(data, limit=MAX_PIXELS):
    Image.MAX_IMAGE_PIXELS = limit
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in {"PNG", "JPEG", "WEBP", "TIFF"}:
                    raise ToolError(415, "Gunakan gambar PNG, JPEG, WebP, atau TIFF")
                if getattr(image, "n_frames", 1) != 1:
                    raise ToolError(415, "Gunakan gambar statis satu halaman; TIFF multipage belum didukung")
                if image.width * image.height > limit:
                    raise ToolError(413, "Resolusi gambar melebihi batas piksel")
                # Pillow's TIFF pixel copy does not retain its IFD metadata.
                exif = image.getexif()
                normalized = ImageOps.exif_transpose(image).copy()
                if image.format == "TIFF":
                    if 274 in exif:
                        del exif[274]
                    if exif:
                        normalized.info["exif"] = exif.tobytes()
                return normalized
        except (Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise ToolError(413, "Resolusi gambar melebihi batas piksel") from None
        except (OSError, ValueError):
            raise ToolError(415, "Gambar tidak dapat dibaca") from None


def pack(binary, info):
    if len(binary) > MAX_RESULT:
        raise ToolError(413, "Hasil melebihi 15 MB; kurangi ukuran atau kualitas")
    header = json.dumps(info, separators=(",", ":")).encode()
    return len(header).to_bytes(4, "big") + header + binary


def compress_image(data, options):
    image = read_image(data)
    original = list(image.size)
    width = number(options, "width", 0, 0, 12000, True)
    height = number(options, "height", 0, 0, 12000, True)
    quality = number(options, "quality", 80, 20, 95, True)
    keep_ratio = flag(options, "keep_ratio")
    remove_metadata = flag(options, "remove_metadata")
    fmt = options.get("format", "webp")
    if fmt not in {"jpeg", "png", "webp", "tiff"}:
        raise ToolError(400, "Format keluaran tidak valid")
    if keep_ratio or not (width and height):
        scale = min([v / original[i] for i, v in enumerate((width, height)) if v] or [1])
        size = (max(1, round(original[0] * scale)), max(1, round(original[1] * scale)))
    else:
        size = (width, height)
    if size[0] * size[1] > MAX_PIXELS:
        raise ToolError(413, "Hasil resize melebihi 24 megapiksel")
    if size != image.size:
        image = image.resize(size, Image.Resampling.LANCZOS)
    # A fresh pixel copy prevents Pillow from implicitly retaining PNG metadata.
    clean = Image.new("RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB", image.size)
    clean.paste(image.convert(clean.mode))
    params = {}
    if not remove_metadata:
        exif = image.getexif()
        # TIFF storage/layout tags must describe the newly encoded pixels.
        for tag in (256, 257, 258, 259, 262, 273, 277, 278, 279, 284, 317, 322, 323, 324, 325, 338, 339):
            if tag in exif:
                del exif[tag]
        if exif:
            params["exif"] = exif.tobytes()
        if image.info.get("icc_profile"):
            params["icc_profile"] = image.info["icc_profile"]
    out = io.BytesIO()
    if fmt == "jpeg":
        rgb = Image.new("RGB", clean.size, "white")
        rgb.paste(clean, mask=clean.getchannel("A") if clean.mode == "RGBA" else None)
        rgb.save(out, "JPEG", quality=quality, optimize=True, progressive=True, **params)
    elif fmt == "webp":
        clean.save(out, "WEBP", quality=quality, method=4, **params)
    elif fmt == "tiff":
        clean.save(out, "TIFF", compression="tiff_lzw", **params)
    else:
        clean.save(out, "PNG", optimize=True, compress_level=9, **params)
    return pack(out.getvalue(), {"format": fmt, "original_pixels": original, "pixels": list(size),
                                 "metadata_removed": remove_metadata})


def image_preview(data, options):
    # Return browser-readable pixels without source metadata or network calls.
    limit = number(options, "max_pixels", MAX_PIXELS, 1, MAX_PIXELS, True)
    image = read_image(data, limit)
    original = list(image.size)
    image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
    clean = Image.new("RGBA" if "A" in image.getbands() else "RGB", image.size)
    clean.paste(image.convert(clean.mode))
    out = io.BytesIO()
    clean.save(out, "PNG", optimize=True)
    return pack(out.getvalue(), {"format": "png", "original_pixels": original,
                                "pixels": list(image.size), "metadata_removed": True})


def open_pdf(data, editing=True, max_pages=30):
    import pymupdf as fitz
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception:
        raise ToolError(422, "PDF tidak dapat dibaca") from None
    if doc.needs_pass or doc.is_encrypted:
        doc.close()
        raise ToolError(422, "PDF bersandi tidak didukung; gunakan salinan tanpa sandi")
    if not 1 <= doc.page_count <= max_pages or doc.xref_length() > 50000:
        doc.close()
        raise ToolError(413, f"PDF maksimal {max_pages} halaman dan 50000 objek")
    if editing:
        # Inspect signature dictionaries as well as SigFlags, including invisible signatures.
        signed = bool(doc.get_sigflags() > 0)
        if not signed:
            for xref in range(1, doc.xref_length()):
                if doc.xref_get_key(xref, "ByteRange")[0] == "array":
                    signed = True
                    break
        if signed:
            doc.close()
            raise ToolError(409, "PDF memiliki tanda tangan kriptografis atau field tanda tangan; gunakan salinan yang belum ditandatangani")
    return doc


def clean_pdf(doc, remove_metadata):
    # Preserve selectable text, links, form values, and pending redaction annotations.
    doc.scrub(attached_files=False, clean_pages=False, embedded_files=False, hidden_text=False,
              javascript=True, metadata=remove_metadata, redactions=False, remove_links=False,
              reset_fields=False, reset_responses=False, thumbnails=False, xml_metadata=remove_metadata)


def merge_pdf(data, options):
    import pymupdf as fitz
    sizes = options.get("sizes")
    if not isinstance(sizes, list) or not 2 <= len(sizes) <= 20:
        raise ToolError(400, "Pilih 2–20 PDF untuk digabungkan")
    if any(isinstance(size, bool) or not isinstance(size, int) or size <= 0 for size in sizes):
        raise ToolError(400, "Ukuran berkas PDF tidak valid")
    if any(size > 10 * 1024 * 1024 for size in sizes) or len(data) > 20 * 1024 * 1024:
        raise ToolError(413, "PDF maksimal 10 MB per berkas dan 20 MB total")
    if sum(sizes) != len(data):
        raise ToolError(400, "Data berkas PDF tidak lengkap atau ukurannya tidak sesuai")
    remove_metadata = flag(options, "remove_metadata")
    page_counts, toc = [], []
    offset = 0
    with fitz.open() as merged:
        for index, size in enumerate(sizes):
            part = data[offset:offset + size]
            offset += size
            if not part[:1024].lstrip().startswith(b"%PDF-"):
                raise ToolError(415, f"Berkas ke-{index + 1} bukan PDF yang valid")
            try:
                source = open_pdf(part, max_pages=100)
            except ToolError as exc:
                raise ToolError(exc.status, f"Berkas ke-{index + 1}: {exc.message}") from None
            with source:
                start = merged.page_count
                if start + source.page_count > 100:
                    raise ToolError(413, "PDF gabungan maksimal 100 halaman")
                if index == 0 and not remove_metadata:
                    keys = {"title", "author", "subject", "keywords", "creator", "producer", "creationDate", "modDate", "trapped"}
                    merged.set_metadata({k: v for k, v in source.metadata.items() if k in keys and v})
                    if source.get_xml_metadata():
                        merged.set_xml_metadata(source.get_xml_metadata())
                bookmarks = source.get_toc(simple=False)
                merged.insert_pdf(source, links=True, annots=True, widgets=True, join_duplicates=False)
                if merged.xref_length() > 100000:
                    raise ToolError(413, "PDF gabungan melebihi batas objek")
                for entry in bookmarks:
                    if entry[2] > 0:
                        entry[2] += start
                    destination = entry[3]
                    if destination.get("kind") == fitz.LINK_GOTO and destination.get("page", -1) >= 0:
                        destination["page"] += start
                    toc.append(entry)
                page_counts.append(source.page_count)
        if toc:
            merged.set_toc(toc)
        clean_pdf(merged, remove_metadata)
        result = merged.tobytes(garbage=4, deflate=True, use_objstms=1)
        return pack(result, {"format": "pdf", "files": len(sizes), "pages": merged.page_count,
                             "source_pages": page_counts, "metadata_removed": remove_metadata})


def compress_pdf(data, options):
    import pymupdf as fitz
    profile = options.get("profile", "lossless")
    if profile not in {"lossless", "balanced", "small"}:
        raise ToolError(400, "Profil kompresi PDF tidak valid")
    quality = number(options, "quality", 75, 20, 95, True)
    dpi = number(options, "dpi", 150, 72, 300, True)
    remove_metadata = flag(options, "remove_metadata")
    with open_pdf(data) as doc:
        changed = 0
        if profile != "lossless":
            candidates = {}
            masks = set()
            for page in doc:
                for image in page.get_images(full=True):
                    if image[1]:
                        masks.add(image[1])
                    candidates.setdefault(image[0], (page.number, image))
            for xref, (page_number, image) in candidates.items():
                # Leave alpha masks, stencils, and unusual image types intact.
                if xref in masks or image[1] or image[4] == 1 or image[5] not in {"DeviceRGB", "DeviceGray", "ICCBased"}:
                    continue
                if image[2] * image[3] > MAX_PIXELS:
                    continue
                page = doc[page_number]
                rects = [r for p in doc for r in p.get_image_rects(xref) if not r.is_empty and not r.is_infinite]
                if not rects:
                    continue
                max_width = max(r.width for r in rects) * dpi / 72
                max_height = max(r.height for r in rects) * dpi / 72
                info = doc.extract_image(xref)
                if info["colorspace"] not in {1, 3}:
                    continue
                with Image.open(io.BytesIO(info["image"])) as source:
                    if source.width * source.height > MAX_PIXELS:
                        continue
                    rgb = source.convert("RGB")
                    scale = min(1, max_width / rgb.width, max_height / rgb.height)
                    if scale < 1:
                        rgb = rgb.resize((max(1, round(rgb.width * scale)), max(1, round(rgb.height * scale))), Image.Resampling.LANCZOS)
                    buffer = io.BytesIO()
                    rgb.save(buffer, "JPEG", quality=quality, optimize=True)
                if len(buffer.getvalue()) < len(info["image"]):
                    page.replace_image(xref, stream=buffer.getvalue())
                    changed += 1
        clean_pdf(doc, remove_metadata)
        result = doc.tobytes(garbage=4, deflate=True, deflate_images=True, deflate_fonts=True, use_objstms=1)
        # Keep sanitization even when optimization does not reduce the size.
        return pack(result, {"format": "pdf", "pages": doc.page_count, "images_reencoded": changed,
                             "profile": profile, "metadata_removed": remove_metadata})


def pdf_preview(data, options):
    import pymupdf as fitz
    page_number = number(options, "page", 1, 1, 30, True)
    with open_pdf(data, editing=False) as doc:
        if page_number > doc.page_count:
            raise ToolError(400, "Nomor halaman tidak tersedia")
        page = doc[page_number - 1]
        if not 1 <= page.rect.width <= 20000 or not 1 <= page.rect.height <= 20000:
            raise ToolError(413, "Dimensi halaman tidak didukung")
        scale = min(1600 / max(page.rect.width, page.rect.height), 2)
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        return json.dumps({"pages": doc.page_count, "page": page_number, "width": page.rect.width,
                           "height": page.rect.height, "image": base64.b64encode(pix.tobytes("jpeg", jpg_quality=82)).decode(),
                           "signature_fields": doc.get_sigflags() > 0}).encode()


def sign_pdf(data, options):
    import pymupdf as fitz
    pdf_size = number(options, "pdf_size", 0, 1, 10 * 1024 * 1024, True)
    signature_bytes = data[pdf_size:]
    if not signature_bytes or len(signature_bytes) > 1024 * 1024:
        raise ToolError(413, "Gambar tanda tangan maksimal 1 MB")
    signature = read_image(signature_bytes, 2_000_000).convert("RGBA")
    if flag(options, "remove_white", False):
        # Only nearly white pixels are made transparent; ink is retained.
        signature.putdata([(r, g, b, 0 if min(r, g, b) >= 245 else a) for r, g, b, a in signature.getdata()])
    if not signature.getchannel("A").getbbox():
        raise ToolError(400, "Tanda tangan kosong")
    buffer = io.BytesIO()
    signature.save(buffer, "PNG")
    placements = options.get("placements")
    if not isinstance(placements, list) or not 1 <= len(placements) <= 30:
        raise ToolError(400, "Tambahkan 1–30 posisi tanda tangan")
    remove_metadata = flag(options, "remove_metadata")
    with open_pdf(data[:pdf_size]) as doc:
        for placement in placements:
            if not isinstance(placement, dict):
                raise ToolError(400, "Posisi tanda tangan tidak valid")
            page_number = number(placement, "page", 0, 1, doc.page_count, True)
            x = number(placement, "x", 0, 0, 1)
            y = number(placement, "y", 0, 0, 1)
            w = number(placement, "w", 0, .005, 1)
            h = number(placement, "h", 0, .005, 1)
            if x + w > 1.000001 or y + h > 1.000001:
                raise ToolError(400, "Posisi tanda tangan melewati halaman")
            page = doc[page_number - 1]
            rect = page.rect
            target = fitz.Rect(x * rect.width, y * rect.height, (x + w) * rect.width, (y + h) * rect.height)
            # Map visual coordinates into the original crop/rotation coordinate system.
            # insert_image rotates counterclockwise; page.rotation is clockwise.
            target = target * page.derotation_matrix
            rotation = page.rotation
            # Use the unrotated crop transformation while inserting (the library's
            # rotated transformation omits a nonzero crop origin).
            page.set_rotation(0)
            page.insert_image(target, stream=buffer.getvalue(), keep_proportion=False, overlay=True, rotate=rotation)
            page.set_rotation(rotation)
        clean_pdf(doc, remove_metadata)
        result = doc.tobytes(garbage=4, deflate=True, use_objstms=1)
        return pack(result, {"format": "pdf", "pages": doc.page_count, "signatures": len(placements),
                             "signature_type": "visual", "metadata_removed": remove_metadata})


def execute(data, mode):
    operation, encoded = mode.split(":", 1)
    try:
        options = json.loads(base64.urlsafe_b64decode(encoded))
    except (ValueError, TypeError):
        raise ToolError(400, "Pengaturan tidak valid") from None
    if not isinstance(options, dict):
        raise ToolError(400, "Pengaturan tidak valid")
    return {"tool-image": compress_image, "tool-image-preview": image_preview, "tool-compress": compress_pdf,
            "tool-preview": pdf_preview, "tool-sign": sign_pdf, "tool-merge": merge_pdf}[operation](data, options)
