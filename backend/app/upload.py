"""Turn an uploaded policy (PDF, DOCX, TXT/MD) into clauses. Files are untrusted: size, page and zip-expansion
caps, type decided by magic bytes (not the filename), text kept in memory only."""
import io
import re
import zipfile
from datetime import date

from .ingest import Clause

MAX_FILE = 5 * 1024 * 1024
MAX_TEXT = 300_000
MAX_CHUNKS = 400
MAX_CHUNK_LEN = 2000
MAX_PAGES = 150
CHUNK = 900


class UploadError(Exception):
    """Message is safe to show to the user."""


def _pdf(data: bytes) -> str:
    from pypdf import PdfReader

    r = PdfReader(io.BytesIO(data))
    if r.is_encrypted:
        raise UploadError("This PDF is password-protected. Please remove the password and upload again.")
    if len(r.pages) > MAX_PAGES:
        raise UploadError(f"This PDF has more than {MAX_PAGES} pages. Please upload just the policy wording.")
    return "\n".join((p.extract_text() or "") for p in r.pages)


def _docx(data: bytes) -> str:
    from docx import Document

    with zipfile.ZipFile(io.BytesIO(data)) as z:  # zip-bomb guard before parsing
        if sum(i.file_size for i in z.infolist()) > 60 * 1024 * 1024:
            raise UploadError("This document is too large to read.")
    d = Document(io.BytesIO(data))
    parts = [p.text for p in d.paragraphs]
    for t in d.tables:
        parts += [" | ".join(c.text for c in row.cells) for row in t.rows]
    return "\n".join(parts)


def extract_text(data: bytes) -> str:
    try:
        if data[:5] == b"%PDF-":
            text = _pdf(data)
        elif data[:2] == b"PK":
            text = _docx(data)
        elif data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
            raise UploadError("Old .doc files are not supported. Please save it as .docx or PDF and upload again.")
        else:
            text = data.decode("utf-8", errors="replace")
            if "\x00" in text[:2000]:
                raise UploadError("This file type is not supported. Please upload a PDF, Word (.docx) or text file.")
    except UploadError:
        raise
    except Exception:
        raise UploadError("We could not read this file. Please check it opens normally and try again.")
    text = re.sub(r"[ \t]+", " ", text).strip()
    if len(text) < 200:
        raise UploadError("We found almost no text. If this is a scanned PDF or photo, please upload a text-based copy "
                          "(most insurers email one) or paste the text into a .txt file.")
    return text[:MAX_TEXT]


def to_chunks(text: str) -> list[str]:
    # ponytail: fixed-size paragraph chunks, no heading detection; add layout-aware sectioning if retrieval misses clauses
    out, buf = [], ""

    def flush():
        nonlocal buf
        if buf.strip():
            out.append(" ".join(buf.split()))
        buf = ""

    for para in re.split(r"\n\s*\n|\n(?=\s*\d+(?:\.\d+)*[.)]?\s+[A-Z])", text):
        while len(para) > 2 * CHUNK:  # very long block: cut at a sentence end if possible
            cut = para.rfind(". ", 0, CHUNK) + 1 or CHUNK
            if len(buf) + cut > CHUNK:
                flush()
            buf += para[:cut] + " "
            para = para[cut:]
        if len(buf) + len(para) > CHUNK:
            flush()
        buf += para + "\n"
    flush()
    return out[:MAX_CHUNKS]


def clauses_from_chunks(chunks: list[str], doc_id: str, name: str = "Your policy") -> dict[str, Clause]:
    out = {}
    for n, t in enumerate(chunks, 1):
        cid = f"{doc_id}:s{n}"
        out[cid] = Clause(cid, doc_id, "policy", date(1900, 1, 1), f"{name}, part {n}", t)
    return out
