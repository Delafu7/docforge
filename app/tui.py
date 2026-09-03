"""Interactive terminal menu for DocForge.

Launch with:

    python -m app.tui

This module is a thin front-end only. Every conversion is performed by the
existing functions in ``app.converters.*`` — no conversion logic is defined
or duplicated here. The HTTP API in ``app.main`` is unaffected.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

MENU_TITLE = "DocForge"
EXIT_LABEL = "Exit"

# Input-extension hints, used for prompt-time validation only. The
# authoritative accept lists live in ``app.main.CONVERSIONS``; these mirror
# them for a friendlier prompt and carry no conversion behaviour.
HTML_EXTS = (".html", ".htm")
MD_EXTS = (".md", ".markdown")
PDF_EXTS = (".pdf",)

DEFAULT_SERVICE_URL = "http://localhost:8000"


# --------------------------------------------------------------------------
# Prompters: an arrow-key UI when stdin/stdout are a TTY, plain text otherwise.
# --------------------------------------------------------------------------
class Prompter:
    """Prompt interface. Implementations must raise KeyboardInterrupt/EOFError
    on Ctrl+C / Ctrl+D so the top-level loop can exit cleanly."""

    def select(self, message: str, choices: list[str]) -> str:
        raise NotImplementedError

    def text(self, message: str, default: str = "") -> str:
        raise NotImplementedError

    def confirm(self, message: str, *, default: bool = False) -> bool:
        raise NotImplementedError


class QuestionaryPrompter(Prompter):
    def __init__(self) -> None:
        import questionary  # noqa: PLC0415 - optional dependency, imported lazily

        self._q = questionary

    def select(self, message: str, choices: list[str]) -> str:
        return self._q.select(message, choices=choices).unsafe_ask()

    def text(self, message: str, default: str = "") -> str:
        return self._q.text(message, default=default).unsafe_ask().strip()

    def confirm(self, message: str, *, default: bool = False) -> bool:
        return self._q.confirm(message, default=default).unsafe_ask()


class PlainPrompter(Prompter):
    """No ANSI, no arrow keys — a numbered menu read over plain stdin."""

    def select(self, message: str, choices: list[str]) -> str:
        print("\n" + message)
        for index, choice in enumerate(choices, 1):
            print(f"  {index}) {choice}")
        while True:
            raw = input("Select a number: ").strip()
            if raw.isdigit() and 1 <= int(raw) <= len(choices):
                return choices[int(raw) - 1]
            print("  Please enter a number from the list.")

    def text(self, message: str, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        raw = input(f"{message}{suffix}: ").strip()
        return raw or default

    def confirm(self, message: str, *, default: bool = False) -> bool:
        hint = "Y/n" if default else "y/N"
        raw = input(f"{message} ({hint}): ").strip().lower()
        if not raw:
            return default
        return raw in ("y", "yes")


def make_prompter() -> Prompter:
    if sys.stdin.isatty() and sys.stdout.isatty():
        try:
            return QuestionaryPrompter()
        except ImportError:
            print("questionary is not installed; using the plain-text menu.")
    return PlainPrompter()


# --------------------------------------------------------------------------
# Shared status output (ASCII only, degrades on every terminal).
# --------------------------------------------------------------------------
def ok(message: str) -> None:
    print(f"OK   - {message}")


def err(message: str) -> None:
    print(f"ERR  - {message}")


def info(message: str) -> None:
    print(f"     - {message}")


# --------------------------------------------------------------------------
# Input helpers: re-prompt on invalid input, blank cancels back to the menu.
# --------------------------------------------------------------------------
def prompt_input_file(p: Prompter, label: str, valid_exts: tuple[str, ...]) -> Path | None:
    while True:
        raw = p.text(f"{label} (blank to cancel)")
        if not raw:
            return None
        path = Path(raw).expanduser()
        if not path.is_file():
            err(f"Not a file: {path}")
            continue
        if path.suffix.lower() not in valid_exts:
            err(f"Expected {', '.join(valid_exts)}, got '{path.suffix or '(none)'}'")
            continue
        return path


def prompt_output_file(p: Prompter, default: Path) -> Path | None:
    while True:
        raw = p.text("Output path (blank to cancel)", default=str(default))
        if not raw:
            return None
        path = Path(raw).expanduser()
        if path.is_dir():
            err(f"Output path is a directory: {path}")
            continue
        if path.exists() and not p.confirm(f"{path} exists - overwrite?", default=False):
            info("Cancelled.")
            return None
        return path


def write_output(path: Path, data: bytes) -> None:
    try:
        path.write_bytes(data)
    except OSError as exc:
        err(f"Could not write {path}: {exc}")
        return
    ok(f"Wrote {path} ({len(data):,} bytes)")


# --------------------------------------------------------------------------
# Operations. Each imports its converter lazily to keep menu startup fast.
# --------------------------------------------------------------------------
def op_html_to_md(p: Prompter) -> None:
    src = prompt_input_file(p, "HTML file to convert", HTML_EXTS)
    if src is None:
        return

    sanitize = p.confirm("Sanitize HTML (strip CSS/JS noise)?", default=True)
    allow_remote = p.confirm("Download remotely-referenced images?", default=False)
    base_url = p.text("Base URL for relative image paths (optional)") if allow_remote else ""

    out = prompt_output_file(p, Path.cwd() / f"{src.stem}.zip")
    if out is None:
        return

    from app.converters.html_to_md import convert

    try:
        result = convert(src.read_bytes(), [], base_url, allow_remote, sanitize)
    except Exception as exc:  # noqa: BLE001 - surface any converter failure as a status line
        err(f"Conversion failed: {exc}")
        return

    write_output(out, result)
    info("ZIP contains converted/converted.md, assets/, and conversion-report.json")


def op_md_to_pdf(p: Prompter) -> None:
    src = prompt_input_file(p, "Markdown file to convert", MD_EXTS)
    if src is None:
        return

    out = prompt_output_file(p, Path.cwd() / f"{src.stem}.pdf")
    if out is None:
        return

    from app.converters.md_to_pdf import convert

    try:
        result = convert(src.read_bytes())
    except Exception as exc:  # noqa: BLE001
        err(f"Conversion failed: {exc}")
        return

    write_output(out, result)


def op_pdf_to_md(p: Prompter) -> None:
    src = prompt_input_file(p, "PDF file to convert", PDF_EXTS)
    if src is None:
        return

    out = prompt_output_file(p, Path.cwd() / f"{src.stem}.md")
    if out is None:
        return

    from app.converters.pdf_to_md import convert

    try:
        result = convert(src.read_bytes())
    except Exception as exc:  # noqa: BLE001
        err(f"Conversion failed: {exc}")
        return

    write_output(out, result)


def op_health(p: Prompter) -> None:
    url = p.text("Service base URL", default=DEFAULT_SERVICE_URL)
    if not url:
        return
    endpoint = url.rstrip("/") + "/health"

    import httpx

    try:
        response = httpx.get(endpoint, timeout=5.0)
    except httpx.HTTPError as exc:
        err(f"{endpoint} is not reachable: {exc}")
        return

    if response.status_code == 200:
        ok(f"{endpoint} -> {response.status_code} {response.text.strip()}")
    else:
        err(f"{endpoint} -> {response.status_code}")


OPERATIONS: list[tuple[str, Callable[[Prompter], None]]] = [
    ("Convert HTML to Markdown", op_html_to_md),
    ("Convert Markdown to PDF", op_md_to_pdf),
    ("Convert PDF to Markdown", op_pdf_to_md),
    ("Check service health", op_health),
]


def main() -> int:
    prompter = make_prompter()
    labels = [label for label, _ in OPERATIONS] + [EXIT_LABEL]
    handlers = {label: handler for label, handler in OPERATIONS}

    try:
        while True:
            choice = prompter.select(MENU_TITLE, labels)
            if choice == EXIT_LABEL:
                return 0
            print()
            handlers[choice](prompter)
            print()
    except (KeyboardInterrupt, EOFError):
        print()
        return 0


if __name__ == "__main__":
    sys.exit(main())
