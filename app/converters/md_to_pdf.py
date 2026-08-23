import markdown
from weasyprint import HTML


def convert(data: bytes) -> bytes:
    md_text = data.decode("utf-8")
    html_body = markdown.markdown(md_text, extensions=["tables", "fenced_code"])
    html_doc = f"<!DOCTYPE html><html><head><meta charset=\"utf-8\"></head><body>{html_body}</body></html>"
    return HTML(string=html_doc).write_pdf()
