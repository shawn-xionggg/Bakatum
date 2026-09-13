"""Turn an uploaded syllabus file (PDF, HTML, or text) into plain text for Claude."""

from io import BytesIO

from bs4 import BeautifulSoup
from pypdf import PdfReader

MAX_CHARS = 80_000


def extract_text(filename: str, data: bytes) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "pdf":
        text = _from_pdf(data)
    elif ext in ("html", "htm"):
        text = _from_html(data)
    elif ext in ("txt", "md"):
        text = data.decode("utf-8", errors="replace")
    else:
        raise ValueError(
            f"Unsupported file type '.{ext}' — upload a .pdf, .html, or .txt syllabus."
        )

    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if not text:
        raise ValueError("Couldn't extract any text from that file.")
    return text[:MAX_CHARS]


def _from_pdf(data: bytes) -> str:
    reader = PdfReader(BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _from_html(data: bytes) -> str:
    soup = BeautifulSoup(data, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text("\n")
