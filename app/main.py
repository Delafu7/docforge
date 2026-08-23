from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.converters.html_to_md import convert as html_to_md
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
        output_ext="md",
        output_mime="text/markdown; charset=utf-8",
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
    app.post(f"/convert/{_name}")(_make_route(_name, _conversion))


@app.get("/health")
def health():
    return {"status": "ok"}


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
