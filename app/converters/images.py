import base64
import mimetypes
import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

# Limits on the *remote-download* step only (step 3 of resolution).
MAX_IMAGES = 50
MAX_TOTAL_BYTES = 25 * 1024 * 1024
TIMEOUT_SECONDS = 10.0
ALLOWED_SCHEMES = {"http", "https"}

# Limits on uploaded local images, enforced at ingestion (see app.main).
MAX_UPLOAD_IMAGES = 100
MAX_UPLOAD_TOTAL_BYTES = 25 * 1024 * 1024

_FILENAME_UNSAFE_RE = re.compile(r"[^a-zA-Z0-9._-]")
_DATA_URI_RE = re.compile(
    r"^data:(?P<mime>[\w.+-]+/[\w.+-]+)(?:;charset=[\w-]+)?(?P<base64>;base64)?,(?P<data>.*)$",
    re.DOTALL,
)
_EXTENSION_OVERRIDES = {"image/jpeg": ".jpg", "image/svg+xml": ".svg"}

_NO_LOCAL_MATCH_REASON = "no_local_match_remote_disabled"

_MAGIC_SIGNATURES = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
]


class _SkipImage(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass
class LocalImage:
    path: str | None
    filename: str
    data: bytes


@dataclass
class ImageReport:
    embedded_files: dict = field(default_factory=dict)
    images_embedded: int = 0
    images_from_upload: int = 0
    images_downloaded: int = 0
    images_skipped: list = field(default_factory=list)
    unused_uploads: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "images_embedded": self.images_embedded,
            "images_from_upload": self.images_from_upload,
            "images_downloaded": self.images_downloaded,
            "images_skipped": self.images_skipped,
            "unused_uploads": self.unused_uploads,
            "warnings": self.warnings,
        }


def embed_images(
    soup: BeautifulSoup,
    base_url: str,
    allow_remote_download: bool,
    images: list[LocalImage],
) -> ImageReport:
    report = ImageReport()
    used_filenames: set = set()
    used_uploads: set = set()
    downloaded_count = 0
    downloaded_bytes = 0

    client = httpx.Client(timeout=TIMEOUT_SECONDS) if allow_remote_download else None
    try:
        for img in soup.find_all("img"):
            src = img.get("src")
            if not src:
                continue

            if src.startswith("data:"):
                try:
                    filename, data = _decode_data_uri(src)
                except _SkipImage as skip:
                    report.images_skipped.append({"src": src, "reason": skip.reason})
                    continue
                filename = _unique_filename(_sanitize_filename(filename), used_filenames)
                report.embedded_files[filename] = data
                report.images_embedded += 1
                img["src"] = f"assets/{filename}"
                continue

            match_idx, warning = _match_local(src, images)
            if match_idx is not None:
                local_image = images[match_idx]
                used_uploads.add(match_idx)
                filename = _unique_filename(_sanitize_filename(local_image.filename), used_filenames)
                report.embedded_files[filename] = local_image.data
                report.images_embedded += 1
                report.images_from_upload += 1
                img["src"] = f"assets/{filename}"
                if warning:
                    report.warnings.append(warning)
                continue

            if allow_remote_download and _is_remote_resolvable(src, base_url):
                if downloaded_count >= MAX_IMAGES:
                    report.images_skipped.append({"src": src, "reason": _NO_LOCAL_MATCH_REASON})
                    report.warnings.append(
                        f"maximum of {MAX_IMAGES} remote downloads reached; "
                        "remaining images left unembedded"
                    )
                    continue
                try:
                    filename, data = _resolve_remote(src, base_url, client, downloaded_bytes)
                except _SkipImage as skip:
                    report.images_skipped.append({"src": src, "reason": skip.reason})
                    if skip.reason == "total download limit exceeded":
                        report.warnings.append(
                            f"total download limit ({MAX_TOTAL_BYTES} bytes) reached; "
                            "remaining images left unembedded"
                        )
                    continue

                filename = _unique_filename(_sanitize_filename(filename), used_filenames)
                report.embedded_files[filename] = data
                report.images_embedded += 1
                report.images_downloaded += 1
                downloaded_count += 1
                downloaded_bytes += len(data)
                img["src"] = f"assets/{filename}"
                continue

            report.images_skipped.append({"src": src, "reason": _NO_LOCAL_MATCH_REASON})
    finally:
        if client is not None:
            client.close()

    for idx, image in enumerate(images):
        if idx not in used_uploads:
            report.unused_uploads.append(image.path or image.filename)

    return report


def _is_remote_resolvable(src: str, base_url: str) -> bool:
    parsed = urlsplit(src)
    if parsed.scheme in ALLOWED_SCHEMES:
        return True
    return parsed.scheme == "" and bool(base_url)


def _normalize_component(path: str) -> str:
    path = path.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


def _normalize_src(src: str) -> str:
    decoded = unquote(src)
    decoded = decoded.split("#", 1)[0].split("?", 1)[0]
    return _normalize_component(decoded)


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _match_local(src: str, images: list[LocalImage]):
    if not images:
        return None, None

    norm_src = _normalize_src(src)
    if not norm_src:
        return None, None

    def effective_path(image: LocalImage) -> str:
        return _normalize_component(image.path) if image.path else _normalize_component(image.filename)

    def basename_of(image: LocalImage) -> str:
        return _basename(_normalize_component(image.filename))

    for idx, image in enumerate(images):
        if effective_path(image) == norm_src:
            return idx, None

    for idx, image in enumerate(images):
        path = effective_path(image)
        if norm_src.endswith(path) or path.endswith(norm_src):
            return idx, None

    src_basename = _basename(norm_src)

    matches = [idx for idx, image in enumerate(images) if basename_of(image) == src_basename]
    if matches:
        warning = None
        if len(matches) > 1:
            warning = f"ambiguous image match for '{src}'; using first matching upload"
        return matches[0], warning

    src_basename_lower = src_basename.lower()
    matches = [idx for idx, image in enumerate(images) if basename_of(image).lower() == src_basename_lower]
    if matches:
        warning = None
        if len(matches) > 1:
            warning = f"ambiguous image match for '{src}'; using first matching upload"
        return matches[0], warning

    return None, None


def _resolve_remote(src: str, base_url: str, client: httpx.Client, total_bytes: int):
    parsed = urlsplit(src)
    if parsed.scheme in ALLOWED_SCHEMES:
        url = src
    else:
        url = urljoin(base_url, src)
        if urlsplit(url).scheme not in ALLOWED_SCHEMES:
            raise _SkipImage(f"unsupported scheme '{urlsplit(url).scheme}'")

    return _download_image(url, client, total_bytes)


def _decode_data_uri(src: str):
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


def detect_image_mime(data: bytes) -> str | None:
    """Sniff magic bytes to identify an image type; never trusts client Content-Type."""
    for signature, mime in _MAGIC_SIGNATURES:
        if data.startswith(signature):
            return mime
    if len(data) >= 12 and data[0:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if _looks_like_svg(data):
        return "image/svg+xml"
    return None


def _looks_like_svg(data: bytes) -> bool:
    head = data[:2048].lstrip()
    if head.startswith(b"<?xml"):
        end = head.find(b"?>")
        head = head[end + 2 :].lstrip() if end != -1 else head
    if head.startswith(b"<!--"):
        end = head.find(b"-->")
        head = head[end + 3 :].lstrip() if end != -1 else head
    return head.startswith(b"<svg") or b"<svg" in data[:4096]


def is_unsafe_relative_path(path: str) -> bool:
    """True if `path` is absolute, carries a drive letter, or contains a ".." segment."""
    if not path:
        return False
    normalized = path.replace("\\", "/")
    if normalized.startswith("/"):
        return True
    if re.match(r"^[A-Za-z]:", normalized):
        return True
    return any(segment == ".." for segment in normalized.split("/"))
