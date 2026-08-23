import base64
import mimetypes
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

MAX_IMAGES = 50
MAX_TOTAL_BYTES = 25 * 1024 * 1024
TIMEOUT_SECONDS = 10.0
ALLOWED_SCHEMES = {"http", "https"}

_FILENAME_UNSAFE_RE = re.compile(r"[^a-zA-Z0-9._-]")
_DATA_URI_RE = re.compile(
    r"^data:(?P<mime>[\w.+-]+/[\w.+-]+)(?:;charset=[\w-]+)?(?P<base64>;base64)?,(?P<data>.*)$",
    re.DOTALL,
)
_EXTENSION_OVERRIDES = {"image/jpeg": ".jpg", "image/svg+xml": ".svg"}


class _SkipImage(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass
class ImageReport:
    embedded_files: dict = field(default_factory=dict)
    images_embedded: int = 0
    images_skipped: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "images_embedded": self.images_embedded,
            "images_skipped": self.images_skipped,
            "warnings": self.warnings,
        }


def embed_images(soup: BeautifulSoup, base_url: str, download_images: bool) -> ImageReport:
    report = ImageReport()
    if not download_images:
        return report

    used_filenames: set = set()
    total_bytes = 0

    with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
        for img in soup.find_all("img"):
            src = img.get("src")
            if not src:
                continue

            if report.images_embedded >= MAX_IMAGES:
                report.warnings.append(
                    f"maximum of {MAX_IMAGES} images reached; remaining images left unembedded"
                )
                break

            try:
                filename, data = _resolve_image(src, base_url, client, total_bytes)
            except _SkipImage as skip:
                report.images_skipped.append({"src": src, "reason": skip.reason})
                if skip.reason == "total download limit exceeded":
                    report.warnings.append(
                        f"total download limit ({MAX_TOTAL_BYTES} bytes) reached; "
                        "remaining images left unembedded"
                    )
                    break
                continue

            filename = _unique_filename(_sanitize_filename(filename), used_filenames)
            report.embedded_files[filename] = data
            report.images_embedded += 1
            total_bytes += len(data)
            img["src"] = f"assets/{filename}"

    return report


def _resolve_image(src: str, base_url: str, client: httpx.Client, total_bytes: int):
    if src.startswith("data:"):
        return _decode_data_uri(src, total_bytes)

    parsed = urlsplit(src)
    if parsed.scheme in ALLOWED_SCHEMES:
        url = src
    elif parsed.scheme == "":
        if not base_url:
            raise _SkipImage("relative URL and no base_url provided")
        url = urljoin(base_url, src)
        if urlsplit(url).scheme not in ALLOWED_SCHEMES:
            raise _SkipImage(f"unsupported scheme '{urlsplit(url).scheme}'")
    else:
        raise _SkipImage(f"unsupported scheme '{parsed.scheme}'")

    return _download_image(url, client, total_bytes)


def _decode_data_uri(src: str, total_bytes: int):
    match = _DATA_URI_RE.match(src)
    if not match:
        raise _SkipImage("malformed data URI")

    mime = match.group("mime").lower()
    if not mime.startswith("image/"):
        raise _SkipImage(f"unsupported content type '{mime}'")
    if not match.group("base64"):
        raise _SkipImage("unsupported data URI encoding")

    try:
        data = base64.b64decode(match.group("data"), validate=False)
    except Exception:
        raise _SkipImage("invalid base64 data")

    if total_bytes + len(data) > MAX_TOTAL_BYTES:
        raise _SkipImage("total download limit exceeded")

    return f"image{_extension_for_mime(mime)}", data


def _download_image(url: str, client: httpx.Client, total_bytes: int):
    try:
        response = client.get(url)
    except httpx.TimeoutException:
        raise _SkipImage("timeout")
    except httpx.HTTPError:
        raise _SkipImage("connection error")

    if response.status_code >= 400:
        raise _SkipImage(f"HTTP {response.status_code}")

    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if not content_type.startswith("image/"):
        raise _SkipImage(f"unsupported content type '{content_type or 'unknown'}'")

    data = response.content
    if total_bytes + len(data) > MAX_TOTAL_BYTES:
        raise _SkipImage("total download limit exceeded")

    return _filename_from_url(url, content_type), data


def _filename_from_url(url: str, content_type: str) -> str:
    path = urlsplit(url).path
    base = path.rsplit("/", 1)[-1]
    if base and "." in base:
        return base
    return f"{base or 'image'}{_extension_for_mime(content_type)}"


def _extension_for_mime(mime: str) -> str:
    mime = mime.lower()
    if mime in _EXTENSION_OVERRIDES:
        return _EXTENSION_OVERRIDES[mime]
    return mimetypes.guess_extension(mime) or ".bin"


def _sanitize_filename(name: str) -> str:
    name = _FILENAME_UNSAFE_RE.sub("_", name)
    return name.strip("._") or "image"


def _unique_filename(base_name: str, used: set) -> str:
    if base_name not in used:
        used.add(base_name)
        return base_name

    if "." in base_name:
        stem, ext = base_name.rsplit(".", 1)
        ext = f".{ext}"
    else:
        stem, ext = base_name, ""

    counter = 1
    while True:
        candidate = f"{stem}_{counter}{ext}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        counter += 1
