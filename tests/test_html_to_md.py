import json
import zipfile
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.converters.html_to_md import ARCHIVE_ROOT, convert
from app.converters.images import LocalImage, MAX_UPLOAD_IMAGES, MAX_UPLOAD_TOTAL_BYTES
from app.main import app

FIXTURES = Path(__file__).parent / "fixtures"
UPLOADS = FIXTURES / "uploads"

PNG_BYTES = (UPLOADS / "logo.png").read_bytes()
JPEG_BYTES = (UPLOADS / "img" / "photo.jpg").read_bytes()

client = TestClient(app)


def _unzip(result: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(BytesIO(result))


def _read_report(zf: zipfile.ZipFile) -> dict:
    return json.loads(zf.read(f"{ARCHIVE_ROOT}/conversion-report.json"))


def _read_md(zf: zipfile.ZipFile) -> str:
    return zf.read(f"{ARCHIVE_ROOT}/{ARCHIVE_ROOT}.md").decode("utf-8")


class FakeResponse:
    def __init__(self, content: bytes, content_type: str, status_code: int = 200):
        self.content = content
        self.status_code = status_code
        self.headers = {"content-type": content_type}


def _mock_get(monkeypatch, handlers: dict):
    def fake_get(self, url, *args, **kwargs):
        handler = handlers.get(url)
        if handler is None:
            raise AssertionError(f"unexpected network call to {url}")
        if isinstance(handler, Exception):
            raise handler
        return handler

    monkeypatch.setattr(httpx.Client, "get", fake_get)


def _fail_get(monkeypatch):
    def fail_get(self, url, *args, **kwargs):
        raise AssertionError("network should not be called when allow_remote_download is False")

    monkeypatch.setattr(httpx.Client, "get", fail_get)


# --- convert() unit tests -------------------------------------------------


def test_headings_use_atx_style_with_no_leaked_markup():
    html = (FIXTURES / "headings.html").read_bytes()
    result = convert(html)
    md = _read_md(_unzip(result))

    assert "# Title" in md
    assert "## Section" in md
    assert "### Sub" in md
    assert "**" not in md.split("Some")[0]
    assert "===" not in md
    assert "---" not in md


def test_no_uploads_no_remote_makes_no_network_calls_and_preserves_src(monkeypatch):
    _fail_get(monkeypatch)

    html = (FIXTURES / "images.html").read_bytes()
    result = convert(html, allow_remote_download=False)
    zf = _unzip(result)

    asset_names = [n for n in zf.namelist() if n.startswith(f"{ARCHIVE_ROOT}/assets/")]
    assert len(asset_names) == 1  # only the data: URI is embedded

    md = _read_md(zf)
    assert "https://example.com/photo.png" in md
    assert "images/relative.png" in md
    assert "data:image/png;base64," not in md

    report = _read_report(zf)
    assert report["images_embedded"] == 1
    assert report["images_from_upload"] == 0
    assert report["images_downloaded"] == 0
    skipped = {entry["src"]: entry["reason"] for entry in report["images_skipped"]}
    assert skipped["https://example.com/photo.png"] == "no_local_match_remote_disabled"
    assert skipped["images/relative.png"] == "no_local_match_remote_disabled"
    assert skipped["https://example.com/unreachable.png"] == "no_local_match_remote_disabled"


def test_data_uri_and_remote_images_embedded_when_allowed(monkeypatch):
    _mock_get(
        monkeypatch,
        {
            "https://example.com/photo.png": FakeResponse(b"absolute-bytes", "image/png"),
            "https://example.com/unreachable.png": httpx.TimeoutException("timed out"),
        },
    )

    html = (FIXTURES / "images.html").read_bytes()
    result = convert(html, allow_remote_download=True)
    zf = _unzip(result)

    asset_names = [n for n in zf.namelist() if n.startswith(f"{ARCHIVE_ROOT}/assets/")]
    assert len(asset_names) == 2

    md = _read_md(zf)
    assert "assets/" in md
    assert "data:image/png;base64," not in md

    report = _read_report(zf)
    assert report["images_embedded"] == 2
    assert report["images_downloaded"] == 1
    skipped_reasons = {entry["src"]: entry["reason"] for entry in report["images_skipped"]}
    assert skipped_reasons["images/relative.png"] == "no_local_match_remote_disabled"
    assert skipped_reasons["https://example.com/unreachable.png"] == "timeout"


def test_relative_url_resolved_against_base_url_when_allowed(monkeypatch):
    _mock_get(
        monkeypatch,
        {
            "https://cdn.example.com/media/images/relative.png": FakeResponse(
                b"relative-bytes", "image/png"
            ),
            "https://example.com/photo.png": FakeResponse(b"absolute-bytes", "image/png"),
            "https://example.com/unreachable.png": httpx.TimeoutException("timed out"),
        },
    )

    html = (FIXTURES / "images.html").read_bytes()
    result = convert(
        html, base_url="https://cdn.example.com/media/", allow_remote_download=True
    )
    zf = _unzip(result)

    report = _read_report(zf)
    assert report["images_embedded"] == 3
    assert report["images_downloaded"] == 2
    assert all(entry["src"] != "images/relative.png" for entry in report["images_skipped"])


def test_non_image_content_type_is_skipped(monkeypatch):
    _mock_get(
        monkeypatch,
        {"https://example.com/photo.png": FakeResponse(b"<html>", "text/html")},
    )

    html = b"<html><body><img src='https://example.com/photo.png'></body></html>"
    result = convert(html, allow_remote_download=True)
    zf = _unzip(result)

    report = _read_report(zf)
    assert report["images_embedded"] == 0
    assert report["images_skipped"] == [
        {"src": "https://example.com/photo.png", "reason": "unsupported content type 'text/html'"}
    ]


def test_local_upload_matched_exactly_and_by_case_insensitive_basename(monkeypatch):
    _fail_get(monkeypatch)

    html = (FIXTURES / "local_images.html").read_bytes()
    images = [
        LocalImage(path="assets/logo.png", filename="logo.png", data=PNG_BYTES),
        LocalImage(path="img/photo.jpg", filename="photo.jpg", data=JPEG_BYTES),
    ]
    result = convert(html, images=images, allow_remote_download=False)
    zf = _unzip(result)

    report = _read_report(zf)
    assert report["images_embedded"] == 2
    assert report["images_from_upload"] == 2
    assert report["images_downloaded"] == 0
    assert report["warnings"] == []
    assert report["images_skipped"] == [
        {"src": "https://example.com/remote.png", "reason": "no_local_match_remote_disabled"}
    ]

    md = _read_md(zf)
    assert "assets/logo.png" in md
    assert "assets/photo.jpg" in md


def test_upload_matched_by_basename_when_no_image_paths(monkeypatch):
    _fail_get(monkeypatch)

    html = b"<html><body><img src='logo.png'></body></html>"
    images = [LocalImage(path=None, filename="logo.png", data=PNG_BYTES)]
    result = convert(html, images=images, allow_remote_download=False)
    zf = _unzip(result)

    report = _read_report(zf)
    assert report["images_from_upload"] == 1
    assert report["images_skipped"] == []


def test_ambiguous_basename_match_adds_warning(monkeypatch):
    _fail_get(monkeypatch)

    html = b"<html><body><img src='images/shared.png'></body></html>"
    images = [
        LocalImage(path="old/shared.png", filename="shared.png", data=b"first"),
        LocalImage(path="new/shared.png", filename="shared.png", data=b"second"),
    ]
    result = convert(html, images=images, allow_remote_download=False)
    zf = _unzip(result)

    report = _read_report(zf)
    assert report["images_from_upload"] == 1
    assert len(report["warnings"]) == 1
    assert "images/shared.png" in report["warnings"][0]

    asset_bytes = [
        zf.read(name) for name in zf.namelist() if name.startswith(f"{ARCHIVE_ROOT}/assets/")
    ]
    assert asset_bytes == [b"first"]


def test_unused_uploads_reported_and_not_written():
    html = b"<html><body><img src='logo.png'></body></html>"
    images = [
        LocalImage(path=None, filename="logo.png", data=PNG_BYTES),
        LocalImage(path="assets/unused.png", filename="unused.png", data=PNG_BYTES),
    ]
    result = convert(html, images=images, allow_remote_download=False)
    zf = _unzip(result)

    report = _read_report(zf)
    assert report["unused_uploads"] == ["assets/unused.png"]

    asset_names = [n for n in zf.namelist() if n.startswith(f"{ARCHIVE_ROOT}/assets/")]
    assert len(asset_names) == 1


def test_upload_takes_priority_over_remote_download(monkeypatch):
    _fail_get(monkeypatch)

    html = b"<html><body><img src='https://example.com/photo.png'></body></html>"
    images = [LocalImage(path=None, filename="photo.png", data=PNG_BYTES)]
    result = convert(html, images=images, allow_remote_download=True)
    zf = _unzip(result)

    report = _read_report(zf)
    assert report["images_from_upload"] == 1
    assert report["images_downloaded"] == 0


def test_zip_has_no_unsafe_member_paths():
    html = (FIXTURES / "headings.html").read_bytes()
    result = convert(html)
    zf = _unzip(result)

    for name in zf.namelist():
        assert not name.startswith("/")
        assert ".." not in name


def test_markdown_ends_with_single_trailing_newline_and_no_triple_blank_lines():
    html = (FIXTURES / "headings.html").read_bytes()
    result = convert(html)
    md = _read_md(_unzip(result))

    assert md.endswith("\n")
    assert not md.endswith("\n\n")
    assert "\n\n\n" not in md


# --- HTTP-level tests for upload validation (app/main.py) -----------------


def test_api_local_upload_matched_by_path_makes_no_network_calls(monkeypatch):
    _fail_get(monkeypatch)

    html = b"<html><body><img src='assets/logo.png'></body></html>"
    response = client.post(
        "/convert/html-to-md",
        files=[
            ("file", ("sample.html", html, "text/html")),
            ("images", ("logo.png", PNG_BYTES, "image/png")),
            ("image_paths", (None, "assets/logo.png")),
        ],
    )
    assert response.status_code == 200

    zf = _unzip(response.content)
    report = _read_report(zf)
    assert report["images_from_upload"] == 1
    assert report["images_downloaded"] == 0


def test_api_allow_remote_download_true_downloads_via_mock(monkeypatch):
    _mock_get(
        monkeypatch,
        {"https://example.com/remote.png": FakeResponse(b"remote-bytes", "image/png")},
    )

    html = b"<html><body><img src='https://example.com/remote.png'></body></html>"
    response = client.post(
        "/convert/html-to-md",
        files=[("file", ("sample.html", html, "text/html"))],
        data={"allow_remote_download": "true"},
    )
    assert response.status_code == 200

    zf = _unzip(response.content)
    report = _read_report(zf)
    assert report["images_downloaded"] == 1


def test_api_unsafe_image_path_returns_400():
    html = b"<html><body><img src='x.png'></body></html>"
    response = client.post(
        "/convert/html-to-md",
        files=[
            ("file", ("sample.html", html, "text/html")),
            ("images", ("x.png", PNG_BYTES, "image/png")),
            ("image_paths", (None, "../../etc/passwd")),
        ],
    )
    assert response.status_code == 400


def test_api_non_image_upload_rejected_by_magic_bytes():
    not_image = (UPLOADS / "not_an_image.txt").read_bytes()
    html = b"<html><body></body></html>"
    response = client.post(
        "/convert/html-to-md",
        files=[
            ("file", ("sample.html", html, "text/html")),
            # lies about content-type; must still be rejected via magic-byte sniffing
            ("images", ("fake.png", not_image, "image/png")),
        ],
    )
    assert response.status_code == 400


def test_api_too_many_uploaded_images_returns_400():
    html = b"<html><body></body></html>"
    files = [("file", ("sample.html", html, "text/html"))]
    for i in range(MAX_UPLOAD_IMAGES + 1):
        files.append(("images", (f"img{i}.png", PNG_BYTES, "image/png")))

    response = client.post("/convert/html-to-md", files=files)
    assert response.status_code == 400


def test_api_oversized_image_uploads_return_413():
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * MAX_UPLOAD_TOTAL_BYTES
    html = b"<html><body></body></html>"
    response = client.post(
        "/convert/html-to-md",
        files=[
            ("file", ("sample.html", html, "text/html")),
            ("images", ("big.png", big, "image/png")),
        ],
    )
    assert response.status_code == 413
