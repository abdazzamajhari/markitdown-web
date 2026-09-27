import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("WEB_API_KEY", "test-secret")
    return TestClient(app)


def upload(client, name, content, key="test-secret"):
    return client.post(
        "/api/convert",
        content=content,
        headers={"Authorization": f"Bearer {key}", "X-Filename": name},
    )


def test_health_and_home(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert "MarkItDown Web" in client.get("/").text


def test_real_text_conversion(client):
    response = upload(client, "catatan.txt", "Judul\nisi\n".encode())
    assert response.status_code == 200, response.text
    assert "Judul" in response.text
    assert response.headers["content-disposition"] == 'attachment; filename="catatan.md"'


def test_real_docx_conversion(client):
    from docx import Document

    doc = Document()
    doc.add_heading("Laporan uji", level=1)
    doc.add_paragraph("Isi penelitian")
    buffer = io.BytesIO()
    doc.save(buffer)
    response = upload(client, "laporan.docx", buffer.getvalue())
    assert response.status_code == 200, response.text
    assert "Laporan uji" in response.text
    assert "Isi penelitian" in response.text


def test_real_xlsx_conversion(client):
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.active.append(["Kelas", "Jumlah"])
    workbook.active.append(["Gamble", 42])
    buffer = io.BytesIO()
    workbook.save(buffer)
    response = upload(client, "data.xlsx", buffer.getvalue())
    assert response.status_code == 200, response.text
    assert "Gamble" in response.text
    assert "42" in response.text


def test_real_pptx_conversion(client):
    from pptx import Presentation

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "Judul slide"
    slide.placeholders[1].text = "Isi slide"
    buffer = io.BytesIO()
    presentation.save(buffer)
    response = upload(client, "presentasi.pptx", buffer.getvalue())
    assert response.status_code == 200, response.text
    assert "Judul slide" in response.text
    assert "Isi slide" in response.text


def test_authentication_and_missing_secret(client, monkeypatch):
    assert upload(client, "a.txt", b"hi", key="wrong").status_code == 401
    monkeypatch.delenv("WEB_API_KEY")
    assert upload(client, "a.txt", b"hi").status_code == 503


@pytest.mark.parametrize("name,content,expected", [
    ("../../secret.txt", b"hi", 400),
    ("evil.html", b"<h1>Hi</h1>", 415),
    ("fake.pdf", b"This is not a PDF", 415),
    ("empty.txt", b"", 400),
    ("bad.txt", b"\xff", 415),
    ("large.txt", b"a" * (10 * 1024 * 1024 + 1), 413),
])
def test_rejects_unsafe_uploads(client, name, content, expected):
    assert upload(client, name, content).status_code == expected


def test_office_zip_is_checked(client):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("something.txt", "no office parts")
    assert upload(client, "fake.docx", buffer.getvalue()).status_code == 415
