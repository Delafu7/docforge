import pymupdf
import pymupdf4llm


def convert(data: bytes) -> bytes:
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        md = pymupdf4llm.to_markdown(doc, show_progress=False)
    finally:
        doc.close()
    return md.encode("utf-8")
