import json
import zipfile
from io import BytesIO
from pathlib import Path

import httpx
import pytest

from app.converters.html_to_md import ARCHIVE_ROOT, convert

FIXTURES = Path(__file__).parent / "fixtures"


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


def test_headings_use_atx_style_with_no_leaked_markup():
    html = (FIXTURES / "headings.html").read_bytes()
    result = convert(html, base_url="", download_images=False)
    md = _read_md(_unzip(result))

    assert "# Title" in md
    assert "## Section" in md
    assert "### Sub" in md
    assert "**" not in md.split("Some")[0]
    assert "===" not in md
    assert "---" not in md


def test_download_images_false_makes_no_network_calls_and_keeps_src(monkeypatch):
    def fail_get(self, url, *args, **kwargs):
        raise AssertionError("network should not be called when download_images=False")

    monkeypatch.setattr(httpx.Client, "get", fail_get)

    html = (FIXTURES / "images.html").read_bytes()
    result = convert(html, base_url="", download_images=False)
    zf = _unzip(result)

    assert not any(name.startswith(f"{ARCHIVE_ROOT}/assets/") for name in zf.namelist())
    md = _read_md(zf)
    assert "https://example.com/photo.png" in md
    assert "data:image/png;base64," in md

    report = _read_report(zf)
    assert report == {"images_embedded": 0, "images_skipped": [], "warnings": []}


def test_data_uri_and_absolute_url_images_are_embedded(monkeypatch):
    _mock_get(
        monkeypatch,
        {
            "https://example.com/photo.png": FakeResponse(b"absolute-bytes", "image/png"),
            "https://example.com/unreachable.png": httpx.TimeoutException("timed out"),
        },
    )

    html = (FIXTURES / "images.html").read_bytes()
    result = convert(html, base_url="", download_images=True)
    zf = _unzip(result)

    asset_names = [n for n in zf.namelist() if n.startswith(f"{ARCHIVE_ROOT}/assets/")]
    assert len(asset_names) == 2

    md = _read_md(zf)
    assert "assets/" in md
    assert "data:image/png;base64," not in md

    report = _read_report(zf)
    assert report["images_embedded"] == 2
    skipped_reasons = {entry["src"]: entry["reason"] for entry in report["images_skipped"]}
    assert skipped_reasons["images/relative.png"] == "relative URL and no base_url provided"
    assert skipped_reasons["https://example.com/unreachable.png"] == "timeout"


def test_relative_url_resolved_against_base_url(monkeypatch):
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
    result = convert(html, base_url="https://cdn.example.com/media/", download_images=True)
    zf = _unzip(result)

    report = _read_report(zf)
    assert report["images_embedded"] == 3
    assert all(entry["src"] != "images/relative.png" for entry in report["images_skipped"])


def test_non_image_content_type_is_skipped(monkeypatch):
    _mock_get(
        monkeypatch,
        {"https://example.com/photo.png": FakeResponse(b"<html>", "text/html")},
    )

    html = b"<html><body><img src='https://example.com/photo.png'></body></html>"
    result = convert(html, base_url="", download_images=True)
    zf = _unzip(result)

    report = _read_report(zf)
    assert report["images_embedded"] == 0
    assert report["images_skipped"] == [
        {"src": "https://example.com/photo.png", "reason": "unsupported content type 'text/html'"}
    ]


def test_zip_has_no_unsafe_member_paths():
    html = (FIXTURES / "headings.html").read_bytes()
    result = convert(html, base_url="", download_images=False)
    zf = _unzip(result)

    for name in zf.namelist():
        assert not name.startswith("/")
        assert ".." not in name


def test_markdown_ends_with_single_trailing_newline_and_no_triple_blank_lines():
    html = (FIXTURES / "headings.html").read_bytes()
    result = convert(html, base_url="", download_images=False)
    md = _read_md(_unzip(result))

    assert md.endswith("\n")
    assert not md.endswith("\n\n")
    assert "\n\n\n" not in md
