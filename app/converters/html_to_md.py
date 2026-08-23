from markdownify import markdownify


def convert(data: bytes) -> bytes:
    html = data.decode("utf-8")
    md = markdownify(html, heading_style="ATX")
    return md.encode("utf-8")
