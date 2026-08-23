import zipfile
from io import BytesIO
from pathlib import Path

from app.converters.html_to_md import convert as html_to_md
from app.converters.md_to_pdf import convert as md_to_pdf
from app.converters.pdf_to_md import convert as pdf_to_md

FIXTURES = Path(__file__).parent / "fixtures"


def test_html_to_md_produces_valid_zip_bundle():
    html_bytes = (FIXTURES / "sample.html").read_bytes()
    result = html_to_md(html_bytes)
    assert isinstance(result, bytes)
    assert len(result) > 0

    zf = zipfile.ZipFile(BytesIO(result))
    text = zf.read("converted/converted.md").decode("utf-8")
    assert "Hello World" in text
    assert "sample" in text


def test_md_to_pdf_produces_valid_pdf():
    md_bytes = (FIXTURES / "sample.md").read_bytes()
    result = md_to_pdf(md_bytes)
    assert isinstance(result, bytes)
    assert len(result) > 0
    assert result.startswith(b"%PDF-")


def test_pdf_to_md_produces_valid_utf8_markdown():
    pdf_bytes = (FIXTURES / "sample.pdf").read_bytes()
    result = pdf_to_md(pdf_bytes)
    assert isinstance(result, bytes)
    assert len(result) > 0
    text = result.decode("utf-8")
    assert "Hello World" in text
