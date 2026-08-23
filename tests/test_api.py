from pathlib import Path

from fastapi.testclient import TestClient

from app.main import MAX_UPLOAD_BYTES, app

FIXTURES = Path(__file__).parent / "fixtures"

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root_serves_ui():
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_html_to_md_success():
    import zipfile
    from io import BytesIO

    data = (FIXTURES / "sample.html").read_bytes()
    response = client.post(
        "/convert/html-to-md",
        files={"file": ("sample.html", data, "text/html")},
        data={"download_images": "false"},
    )
    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="sample.zip"'
    assert response.headers["content-type"] == "application/zip"

    zf = zipfile.ZipFile(BytesIO(response.content))
    md = zf.read("converted/converted.md").decode("utf-8")
    assert "Hello World" in md
    assert "converted/conversion-report.json" in zf.namelist()


def test_md_to_pdf_success():
    data = (FIXTURES / "sample.md").read_bytes()
    response = client.post(
        "/convert/md-to-pdf",
        files={"file": ("sample.md", data, "text/markdown")},
    )
    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="sample.pdf"'
    assert response.content.startswith(b"%PDF-")


def test_pdf_to_md_success():
    data = (FIXTURES / "sample.pdf").read_bytes()
    response = client.post(
        "/convert/pdf-to-md",
        files={"file": ("sample.pdf", data, "application/pdf")},
    )
    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="sample.md"'
    assert b"Hello World" in response.content


def test_wrong_extension_returns_400():
    data = (FIXTURES / "sample.md").read_bytes()
    response = client.post(
        "/convert/html-to-md",
        files={"file": ("sample.md", data, "text/markdown")},
    )
    assert response.status_code == 400
    assert "detail" in response.json()


def test_oversized_upload_returns_413():
    data = b"a" * (MAX_UPLOAD_BYTES + 1)
    response = client.post(
        "/convert/html-to-md",
        files={"file": ("big.html", data, "text/html")},
    )
    assert response.status_code == 413
    assert "detail" in response.json()
