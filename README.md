# doc-converter

A self-contained document conversion service (FastAPI + Docker). Three conversions
are exposed over HTTP:

- `POST /convert/html-to-md` — `.html` / `.htm` → `.md`
- `POST /convert/md-to-pdf` — `.md` / `.markdown` → `.pdf`
- `POST /convert/pdf-to-md` — `.pdf` → `.md`

Plus `GET /health` for readiness checks and `GET /` for a minimal browser UI.

The service is ephemeral: it is meant to be built, started, used for one or more
conversions, and torn down — no database, no auth, no persistence.

## Request contract

Every conversion endpoint takes a single uploaded file under the field name `file`
(`multipart/form-data`) and returns the converted file as a binary download.

- `200` — file bytes, correct `Content-Type`, and
  `Content-Disposition: attachment; filename="<name>.<ext>"`
- `400` — wrong extension or wrong MIME type: `{"detail": "<reason>"}`
- `413` — upload larger than 25 MB: `{"detail": "<reason>"}`
- `422` — conversion failed: `{"detail": "<reason>"}`

## Run locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000/ in a browser for the upload UI, or use curl (see below).

## Run with Docker

```bash
docker build -t doc-converter .
docker run -d -p 8000:8000 --name doc-converter doc-converter
curl http://localhost:8000/health
docker rm -f doc-converter
```

## Endpoints

### `GET /health`

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

### `POST /convert/html-to-md`

Unlike the other two endpoints, this one returns a ZIP bundle rather than a single
file, so images referenced by the HTML can be embedded alongside the Markdown.

```bash
curl -F "file=@tests/fixtures/sample.html" \
  -F "base_url=https://example.com/" \
  -F "download_images=true" \
  http://localhost:8000/convert/html-to-md -OJ
# writes sample.zip
```

Form fields (both optional):

- `base_url` — string, default empty. Used to resolve relative `src` / `href`
  values on images. Relative image URLs are skipped if `base_url` is not set.
- `download_images` — bool, default `true`. When `false`, no network calls are
  made and every `src` is left untouched.

The response is `application/zip` with `Content-Disposition: attachment;
filename="<stem>.zip"`. Unzipped, the archive contains one top-level folder:

```
converted/
  converted.md
  assets/                  # only present when at least one image was embedded
  conversion-report.json
```

`conversion-report.json` records what happened to each image:

```json
{"images_embedded": 4, "images_skipped": [{"src": "...", "reason": "timeout"}], "warnings": []}
```

Image handling, per `<img>` tag, in order:

1. `data:` URI — decoded and written to `assets/`.
2. Absolute `http(s)` URL — downloaded and written to `assets/`.
3. Relative URL — resolved against `base_url` and downloaded; skipped if
   `base_url` is empty.

Limits (an image that exceeds one of these is skipped and recorded in the
report; the request still returns `200`):

- `http` / `https` schemes only
- 10s timeout per image
- max 50 images per document
- max 25 MB downloaded in total
- `image/*` content types only

### `POST /convert/md-to-pdf`

```bash
curl -F "file=@tests/fixtures/sample.md" \
  http://localhost:8000/convert/md-to-pdf -OJ
# writes sample.pdf
```

### `POST /convert/pdf-to-md`

```bash
curl -F "file=@tests/fixtures/sample.pdf" \
  http://localhost:8000/convert/pdf-to-md -OJ
# writes sample.md
```

## Calling the service from a GitHub Actions workflow

See `.github/workflows/convert.yml` for a working reference. The pattern:

1. `docker build -t doc-converter .`
2. `docker run -d -p 8000:8000 --name doc-converter doc-converter`
3. Poll `GET /health` until it returns `200` (with a timeout).
4. `curl -F "file=@$source_path" http://localhost:8000/convert/$conversion -OJ`
   to write the converted file, or capture headers with `-D -` to parse
   `Content-Disposition` for the output filename.
5. Fail the step on any non-2xx response.
6. `docker rm -f doc-converter` in an `if: always()` cleanup step.

Trigger it manually via `workflow_dispatch`, supplying `source_path`, `conversion`
(one of `html-to-md`, `md-to-pdf`, `pdf-to-md`), and `output_dir`.

## Tests

```bash
pip install -r requirements.txt pytest httpx
pytest
```
