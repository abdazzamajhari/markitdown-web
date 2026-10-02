"""Check file integrity, geometry, privacy controls and bounded offline operations."""
import base64
import io
import json

import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app import main


@pytest.fixture
def client(monkeypatch):
    main.tool_requests.clear()
    main.recent_requests.clear()
    main.region_requests.clear()
    monkeypatch.delenv("SUMOPOD_API_KEY", raising=False)
    monkeypatch.setattr(main.httpx, "post", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("external provider called")))
    return TestClient(main.app)


def upload(client, path, data, options, name="document.pdf"):
    return client.post(path, content=data, headers={"X-Filename": name,
        "X-Options": base64.urlsafe_b64encode(json.dumps(options).encode()).decode()})


def image_bytes(size=(800, 400), fmt="PNG", metadata=False):
    image = Image.new("RGB", size, "white")
    ImageDraw.Draw(image).rectangle((20, 20, size[0] - 20, size[1] - 20), fill="navy")
    buffer = io.BytesIO()
    exif = Image.Exif()
    if metadata:
        exif[315] = "Private author"
    image.save(buffer, fmt, exif=exif)
    return buffer.getvalue()


def pdf_bytes(rotation=0, crop=False, photo=False):
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((60, 80), "PRIVATE DOCUMENT TEXT")
    if photo:
        noise = Image.effect_noise((1600, 1200), 50).convert("RGB")
        buffer = io.BytesIO()
        noise.save(buffer, "PNG")
        page.insert_image(fitz.Rect(50, 120, 550, 700), stream=buffer.getvalue())
    if crop:
        page.set_cropbox(fitz.Rect(30, 20, 570, 780))
    page.set_rotation(rotation)
    page = doc.new_page(width=300, height=400)
    page.insert_text((20, 40), "SECOND PAGE")
    doc.set_metadata({"author": "Private author", "title": "Private title"})
    doc.set_xml_metadata("<xmp>private_metadata</xmp>")
    return doc.tobytes()


def test_image_resize_formats_and_metadata(client):
    original = image_bytes(metadata=True)
    for fmt in ("webp", "jpeg", "png"):
        response = upload(client, "/api/image-compress", original, {"format": fmt, "width": 200, "height": 200}, "photo.png")
        assert response.status_code == 200, response.text
        with Image.open(io.BytesIO(response.content)) as image:
            assert image.size == (200, 100)
            assert not image.getexif()
            assert "icc_profile" not in image.info
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
    exact = upload(client, "/api/image-compress", original, {"format": "png", "width": 200, "height": 200, "keep_ratio": False}, "photo.png")
    assert Image.open(io.BytesIO(exact.content)).size == (200, 200)
    height_only = upload(client, "/api/image-compress", original, {"height": 100}, "photo.png")
    assert Image.open(io.BytesIO(height_only.content)).size == (200, 100)


def test_transparency_orientation_and_metadata_opt_out(client):
    image = Image.new("RGBA", (100, 50), (255, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((10, 10, 90, 40), fill=(0, 0, 0, 255))
    buf = io.BytesIO(); image.save(buf, "PNG")
    response = upload(client, "/api/image-compress", buf.getvalue(), {"format": "png"}, "sig.png")
    output = Image.open(io.BytesIO(response.content)).convert("RGBA")
    assert output.getpixel((0, 0))[3] == 0
    response = upload(client, "/api/image-compress", buf.getvalue(), {"format": "jpeg"}, "sig.png")
    assert min(Image.open(io.BytesIO(response.content)).getpixel((0, 0))) > 240
    image = Image.new("RGB", (120, 60), "white"); exif = Image.Exif(); exif[274] = 6; exif[315] = "Author"
    buf = io.BytesIO(); image.save(buf, "JPEG", exif=exif)
    response = upload(client, "/api/image-compress", buf.getvalue(), {"format": "jpeg", "remove_metadata": False}, "oriented.jpg")
    output = Image.open(io.BytesIO(response.content))
    assert output.size == (60, 120)
    assert output.getexif()[315] == "Author" and 274 not in output.getexif()


def test_lossless_pdf_preserves_text_forms_and_dimensions(client):
    original = pdf_bytes()
    source = fitz.open(stream=original, filetype="pdf")
    widget = fitz.Widget(); widget.field_name = "Name"; widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    widget.rect = fitz.Rect(50, 100, 200, 125); widget.field_value = "Keep my value"
    source[0].add_widget(widget)
    original = source.tobytes()
    response = upload(client, "/api/pdf-compress", original, {"profile": "lossless"})
    assert response.status_code == 200, response.text
    with fitz.open(stream=response.content, filetype="pdf") as output:
        assert output.page_count == 2
        assert "PRIVATE DOCUMENT TEXT" in output[0].get_text()
        assert output[0].rect == source[0].rect
        assert not output.metadata.get("author") and not output.get_xml_metadata()
        assert list(output[0].widgets())[0].field_value == "Keep my value"
    keep = upload(client, "/api/pdf-compress", original, {"remove_metadata": False})
    with fitz.open(stream=keep.content, filetype="pdf") as output:
        assert output.metadata["author"] == "Private author"


def test_lossy_pdf_smaller_with_text_intact(client):
    original = pdf_bytes(photo=True)
    response = upload(client, "/api/pdf-compress", original, {"profile": "small", "quality": 55, "dpi": 96})
    assert response.status_code == 200, response.text
    assert len(response.content) < len(original)
    assert json.loads(response.headers["x-document-info"])["images_reencoded"] == 1
    with fitz.open(stream=response.content, filetype="pdf") as output:
        assert "PRIVATE DOCUMENT TEXT" in output[0].get_text()


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("crop", [False, True])
def test_signature_matches_preview_geometry(client, rotation, crop):
    original = pdf_bytes(rotation, crop)
    image = Image.new("RGBA", (200, 80), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((10, 10, 190, 70), fill=(200, 0, 0, 255))
    buf = io.BytesIO(); image.save(buf, "PNG"); signature = buf.getvalue()
    placement = {"page": 1, "x": .2, "y": .4, "w": .3, "h": .1}
    response = upload(client, "/api/pdf-sign", original + signature, {"pdf_size": len(original), "placements": [placement]})
    assert response.status_code == 200, response.text
    source = fitz.open(stream=original, filetype="pdf")
    with fitz.open(stream=response.content, filetype="pdf") as output:
        assert output[0].rect.width == source[0].rect.width
        assert output[0].rect.height == source[0].rect.height
        assert "PRIVATE DOCUMENT TEXT" in output[0].get_text()
        assert "SECOND PAGE" in output[1].get_text()
        pix = output[0].get_pixmap()
        rendered = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        r, g, b = rendered.getpixel((round(pix.width * .35), round(pix.height * .45)))
        assert r > 150 and g < 80 and b < 80


def test_preview_and_multiple_signatures(client):
    original = pdf_bytes()
    preview = upload(client, "/api/pdf-editor-preview", original, {"page": 2})
    assert preview.status_code == 200
    assert preview.json()["pages"] == 2 and preview.json()["width"] == 300
    assert Image.open(io.BytesIO(base64.b64decode(preview.json()["image"]))).width <= 1600
    sig = image_bytes((200, 100))
    positions = [{"page": p, "x": .1, "y": .5, "w": .4, "h": .15} for p in (1, 2)]
    response = upload(client, "/api/pdf-sign", original + sig, {"pdf_size": len(original), "placements": positions, "remove_white": True})
    assert response.status_code == 200, response.text
    assert json.loads(response.headers["x-document-info"])["signatures"] == 2


def test_invalid_settings_encrypted_signed_and_limits(client):
    original = pdf_bytes()
    assert upload(client, "/api/pdf-editor-preview", original, {"page": 3}).status_code == 400
    assert upload(client, "/api/pdf-compress", b"%PDF-broken", {}).status_code == 422
    assert upload(client, "/api/image-compress", image_bytes(), {"width": 12000, "height": 12000, "keep_ratio": False}, "a.png").status_code == 413
    assert upload(client, "/api/image-compress", image_bytes(), {"width": -1}, "a.png").status_code == 400
    sig = image_bytes((200, 100))
    positions = [{"page": 1, "x": .9, "y": .9, "w": .2, "h": .2}]
    assert upload(client, "/api/pdf-sign", original + sig, {"pdf_size": len(original), "placements": positions}).status_code == 400
    doc = fitz.open(stream=original, filetype="pdf")
    encrypted = doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="secret")
    assert upload(client, "/api/pdf-compress", encrypted, {}).status_code == 422
    widget = fitz.Widget(); widget.field_name = "Signature"; widget.field_type = fitz.PDF_WIDGET_TYPE_SIGNATURE; widget.rect = fitz.Rect(50, 120, 200, 150)
    doc[0].add_widget(widget); signed = doc.tobytes()
    assert upload(client, "/api/pdf-compress", signed, {}).status_code == 409
    assert upload(client, "/api/pdf-sign", signed + sig, {"pdf_size": len(signed), "placements": [{"page": 1, "x": .1, "y": .5, "w": .2, "h": .1}]}).status_code == 409
    doc = fitz.open()
    for _ in range(31): doc.new_page()
    assert upload(client, "/api/pdf-compress", doc.tobytes(), {}).status_code == 413
    invalid = client.post("/api/pdf-compress", content=original, headers={"X-Filename": "a.pdf", "X-Options": "@broken"})
    assert invalid.status_code == 400


def test_private_ocr_and_explicit_external_consent(client, monkeypatch):
    monkeypatch.setenv("SUMOPOD_API_KEY", "configured-but-not-used")
    original = Image.new("RGB", (700, 200), "white")
    ImageDraw.Draw(original).text((30, 30), "PRIVATE LOCAL OCR TEXT", fill="black", font_size=32)
    buffer = io.BytesIO(); original.save(buffer, "PNG")
    response = client.post("/api/convert", content=buffer.getvalue(), headers={"X-Filename": "a.png"})
    assert response.status_code == 200, response.text
    assert response.headers["x-ocr-engine"] == "tesseract" and "PRIVATE" in response.text
    assert client.post("/api/pdf-language-check", json={"text": "Private local OCR"}).status_code == 400
