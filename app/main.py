import json
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.converters.html_to_md import ARCHIVE_ROOT, convert as html_to_md
from app.converters.images import (
    MAX_UPLOAD_IMAGES,
    MAX_UPLOAD_TOTAL_BYTES,
    LocalImage,
    detect_image_mime,
    is_unsafe_relative_path,
)
from app.converters.md_to_pdf import convert as md_to_pdf
from app.converters.pdf_to_md import convert as pdf_to_md

MAX_UPLOAD_BYTES = 25 * 1024 * 1024

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI()


@dataclass(frozen=True)
class Conversion:
    extensions: tuple[str, ...]
    mime_types: tuple[str, ...]
    output_ext: str
    output_mime: str
    convert: callable


CONVERSIONS: dict[str, Conversion] = {
    "html-to-md": Conversion(
        extensions=(".html", ".htm"),
        mime_types=("text/html", "application/xhtml+xml"),
        output_ext="zip",
        output_mime="application/zip",
        convert=html_to_md,
    ),
    "md-to-pdf": Conversion(
        extensions=(".md", ".markdown"),
        mime_types=("text/markdown", "text/x-markdown", "text/plain"),
        output_ext="pdf",
        output_mime="application/pdf",
        convert=md_to_pdf,
    ),
    "pdf-to-md": Conversion(
        extensions=(".pdf",),
        mime_types=("application/pdf",),
        output_ext="md",
        output_mime="text/markdown; charset=utf-8",
        convert=pdf_to_md,
    ),
}


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Uploaded file exceeds 25 MB limit")
    return data


GENERIC_MIME_TYPES = {"application/octet-stream", ""}


def _validate(conversion: Conversion, file: UploadFile) -> str:
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix not in conversion.extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file extension '{suffix}'. Expected one of {conversion.extensions}",
        )
    content_type = file.content_type or ""
    if content_type not in GENERIC_MIME_TYPES and content_type not in conversion.mime_types:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported content type '{file.content_type}'. Expected one of {conversion.mime_types}",
        )
    return Path(filename).stem or "output"


def _make_route(name: str, conversion: Conversion):
    async def route(file: UploadFile):
        stem = _validate(conversion, file)
        data = await _read_upload(file)

        try:
            result = conversion.convert(data)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Conversion failed: {exc}") from exc

        filename = f"{stem}.{conversion.output_ext}"
        return Response(
            content=result,
            media_type=conversion.output_mime,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    route.__name__ = f"convert_{name.replace('-', '_')}"
    return route


for _name, _conversion in CONVERSIONS.items():
    if _name == "html-to-md":
        continue
    app.post(f"/convert/{_name}")(_make_route(_name, _conversion))


async def _collect_local_images(
    images: list[UploadFile], image_paths: list[str]
) -> list[LocalImage]:
    images = [img for img in images if img.filename]

    if len(images) > MAX_UPLOAD_IMAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Too many uploaded images (max {MAX_UPLOAD_IMAGES})",
        )

    if image_paths and len(image_paths) != len(images):
        raise HTTPException(
            status_code=400,
            detail="image_paths must have exactly one entry per uploaded image",
        )

    for path in image_paths:
        if is_unsafe_relative_path(path):
            raise HTTPException(status_code=400, detail=f"Unsafe image_paths value: '{path}'")

    local_images = []
    total_bytes = 0
    for idx, upload in enumerate(images):
        content = await upload.read(MAX_UPLOAD_TOTAL_BYTES + 1)
        total_bytes += len(content)
        if total_bytes > MAX_UPLOAD_TOTAL_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Uploaded images exceed {MAX_UPLOAD_TOTAL_BYTES} bytes total",
            )
        if detect_image_mime(content) is None:
            raise HTTPException(
                status_code=400,
                detail=f"Uploaded image '{upload.filename}' is not a recognized image type",
            )
        path = image_paths[idx] if idx < len(image_paths) and image_paths[idx] else None
        local_images.append(LocalImage(path=path, filename=Path(upload.filename).name, data=content))

    return local_images


@app.post("/convert/html-to-md")
async def convert_html_to_md(
    file: UploadFile,
    images: list[UploadFile] | None = File(None),
    image_paths: list[str] | None = Form(None),
    base_url: str = Form(""),
    allow_remote_download: bool = Form(False),
):
    conversion = CONVERSIONS["html-to-md"]
    stem = _validate(conversion, file)
    data = await _read_upload(file)
    local_images = await _collect_local_images(images or [], image_paths or [])

    try:
        result = html_to_md(data, local_images, base_url, allow_remote_download)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Conversion failed: {exc}") from exc

    filename = f"{stem}.zip"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    report_json = _extract_conversion_report(result)
    if report_json is not None:
        headers["X-Conversion-Report"] = report_json

    return Response(
        content=result,
        media_type=conversion.output_mime,
        headers=headers,
    )


def _extract_conversion_report(zip_bytes: bytes) -> str | None:
    try:
        with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
            raw = zf.read(f"{ARCHIVE_ROOT}/conversion-report.json")
    except KeyError:
        return None
    return json.dumps(json.loads(raw), separators=(",", ":"))


@app.get("/health")
def health():
    return {"status": "ok"}


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
