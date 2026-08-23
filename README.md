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
  -F "images=@assets/logo.png" \
  -F "image_paths=assets/logo.png" \
  -F "base_url=https://example.com/" \
  -F "allow_remote_download=false" \
  http://localhost:8000/convert/html-to-md -OJ
# writes sample.zip
```

Form fields:

- `images` — repeated file field, zero or more local image files (optional).
  Typically the contents of an assets folder sitting next to the HTML file —
  e.g. what a GitHub Actions checkout already has on disk. Uploading images
  this way avoids a network round-trip and works for private or relative
  paths that a remote download could never reach.
- `image_paths` — repeated string field, optional, one entry per file in
  `images`, giving that file's path relative to the HTML document (e.g.
  `assets/logo.png`). When omitted for an entry, that upload's own filename
  is used instead.
- `base_url` — string, default empty. Used to resolve relative `src` values
  against a remote host when remote download is allowed.
- `allow_remote_download` — bool, **default `false`**. Downloading images
  over the network is opt-in: with no uploads and this flag unset, the
  endpoint never makes an HTTP call.

The response is `application/zip` with `Content-Disposition: attachment;
filename="<stem>.zip"`, and an `X-Conversion-Report` header carrying the same
JSON as `conversion-report.json` (handy for a UI to show a summary without
unzipping the response). Unzipped, the archive contains one top-level folder:

```
converted/
  converted.md
  assets/                  # only present when at least one image was embedded
  conversion-report.json
```

`conversion-report.json` records what happened to each image:

```json
{
  "images_embedded": 6,
  "images_from_upload": 5,
  "images_downloaded": 1,
  "images_skipped": [{"src": "...", "reason": "no_local_match_remote_disabled"}],
  "unused_uploads": ["assets/unused.png"],
  "warnings": []
}
```

- `images_embedded` — total images written to `assets/`, from any source.
- `images_from_upload` — subset of those embedded from an `images` upload.
- `images_downloaded` — subset of those embedded via a remote download.
- `images_skipped` — `<img>` tags left untouched, with a reason.
- `unused_uploads` — uploaded images no `<img>` tag referenced; not written
  to `assets/`.
- `warnings` — non-fatal notices, e.g. an ambiguous upload match.

Image resolution, per `<img>` tag, first match wins:

1. `data:` URI — decoded and written to `assets/`.
2. Local upload match (see matching rules below) — the uploaded bytes are
   copied into `assets/`.
3. Remote `http(s)` URL, or a relative URL resolvable against a non-empty
   `base_url` — downloaded, **only if `allow_remote_download` is `true`**.
4. Otherwise — the original `src` is left untouched, and the image is
   recorded in `images_skipped` with reason `no_local_match_remote_disabled`.

Matching rules for step 2 (the `src` is normalized first: URL-decoded, query
string and fragment stripped, a leading `./` dropped, separators normalized
to `/`), tried in order:

1. Exact match against a supplied `image_paths` value.
2. Suffix match — the `src` ends with a supplied path, or a supplied path
   ends with the `src` (handles e.g. `../assets/logo.png` vs `assets/logo.png`).
3. Basename match against the upload's filename.
4. Case-insensitive basename match.

An ambiguous basename match (two uploads sharing a basename, with no path to
disambiguate) resolves to the first matching upload and adds a `warnings`
entry naming the `src`.

Limits on uploads (an image that exceeds one of these fails the whole
request):

- max 100 uploaded images — `400` if exceeded
- max 25 MB total across all uploads — `413` if exceeded
- `image/*` content types only, verified by sniffing magic bytes (never the
  client-supplied `Content-Type`) — `400` on a non-image upload
- any `image_paths` value containing an absolute path, a drive letter, or a
  `..` segment — `400`

Limits on remote downloads (step 3 only; an image that exceeds one of these
is skipped and recorded in the report, non-fatal — the request still returns
`200`):

- `http` / `https` schemes only
- 10s timeout per image
- max 50 remote downloads per document
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
(one of `html-to-md`, `md-to-pdf`, `pdf-to-md`), and `output_dir`. For
`html-to-md`, an optional `assets_dir` input uploads every file under that
directory as a local image (with its path relative to `source_path`'s
directory sent as `image_paths`), so images already on disk in the checkout
are embedded without any network call.

## Deploying the UI to GitHub Pages

GitHub Pages only serves static files — it cannot run the FastAPI backend
(Docker, WeasyPrint, PyMuPDF). So the split is: the backend runs on a real
host you choose, and `app/static/` (the browser UI) is published to GitHub
Pages and configured to call that backend's URL.

1. **Deploy the backend somewhere that runs Docker containers** — e.g.
   Fly.io, Render, Railway, or your own VPS — using the existing
   `Dockerfile`. Whatever you pick, you get back a base URL like
   `https://doc-converter.example.com`.
2. **Allow the Pages origin to call it.** Set the backend's `ALLOWED_ORIGINS`
   environment variable to your Pages URL, e.g.
   `ALLOWED_ORIGINS=https://<user>.github.io` (comma-separate multiple
   origins). Without this, the browser's CORS check blocks the request — the
   API is closed to cross-origin calls by default.
3. **Point the UI at the backend.** Edit the `API_BASE` constant near the top
   of `app/static/app.js` to that same base URL (no trailing slash), commit,
   and push to `main`.
4. **Enable Pages once, in the repo's GitHub settings** — Settings → Pages →
   Source → "GitHub Actions". After that, `.github/workflows/pages.yml`
   publishes `app/static/` on every push to `main` that touches it (or via
   `workflow_dispatch`).

Local development and the Docker image are unaffected: when `API_BASE` is
empty (its default) and `ALLOWED_ORIGINS` is unset, the UI keeps talking to
whatever origin served it, same as before this split existed.

## Tests

```bash
pip install -r requirements.txt pytest httpx
pytest
```
