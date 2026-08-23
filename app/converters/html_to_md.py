import json
import re
import zipfile
from io import BytesIO

from bs4 import BeautifulSoup, Comment
from markdownify import markdownify

from app.converters.images import LocalImage, embed_images

_HEADING_RE = re.compile(r"^h[1-6]$")
_BLANK_LINES_RE = re.compile(r"\n{3,}")

ARCHIVE_ROOT = "converted"


def convert(
    html: bytes,
    images: list[LocalImage] | None = None,
    base_url: str = "",
    allow_remote_download: bool = False,
) -> bytes:
    soup = BeautifulSoup(html.decode("utf-8"), "html.parser")

    _strip_unwanted(soup)
    _clean_headings(soup)

    report = embed_images(soup, base_url, allow_remote_download, images or [])

    md = markdownify(str(soup), heading_style="ATX", bullets="-", code_language="")
    md = _normalize_markdown(md)

    return _build_zip(md, report)


def _strip_unwanted(soup: BeautifulSoup) -> None:
    for tag in soup(["script", "style", "noscript", "iframe"]):
        tag.decompose()
    for comment in soup.find_all(string=lambda node: isinstance(node, Comment)):
        comment.extract()


def _clean_headings(soup: BeautifulSoup) -> None:
    for heading in soup.find_all(_HEADING_RE):
        text = " ".join(heading.get_text().split())
        heading.clear()
        heading.append(text)


def _normalize_markdown(md: str) -> str:
    md = _BLANK_LINES_RE.sub("\n\n", md)
    return md.strip("\n") + "\n"


def _build_zip(md: str, report) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{ARCHIVE_ROOT}/{ARCHIVE_ROOT}.md", md)
        for filename, data in report.embedded_files.items():
            zf.writestr(f"{ARCHIVE_ROOT}/assets/{filename}", data)
        zf.writestr(
            f"{ARCHIVE_ROOT}/conversion-report.json",
            json.dumps(report.to_dict(), indent=2),
        )
    return buffer.getvalue()
