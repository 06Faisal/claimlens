"""Malicious / disguised uploads must be rejected; legitimate variants must still work."""
import io
import zipfile

from decimal import Decimal as D

import pytest
from docx import Document
from fastapi.testclient import TestClient

from app.engine import Facts
from app.main import create_app
from app.mock_llm import MockLLM
from app.schemas import Cited, Rules
from tests.test_upload import POLICY, client, pdf_bytes, up


def _zip(files: dict) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        for k, v in files.items():
            z.writestr(k, v)
    return b.getvalue()


def _docx_with(extra=None, ctypes=None) -> bytes:
    d = Document()
    d.add_paragraph(POLICY)
    buf = io.BytesIO()
    d.save(buf)
    src = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
    files = {n: src.read(n) for n in src.namelist()}
    if ctypes:
        files["[Content_Types].xml"] += ctypes.encode()
    for k, v in (extra or {}).items():
        if k.endswith(".rels") and k in files:  # add a relationship, keep the real ones so the document still opens
            v = files[k].replace(b"</Relationships>", v.replace(b"<Relationships>", b"").replace(b"</Relationships>", b"") + b"</Relationships>")
        files[k] = v
    return _zip(files)


EXTERNAL = b'<Relationships><Relationship Id="1" Type="http://x/attachedTemplate" Target="http://evil/t.dotm" TargetMode="External"/></Relationships>'
HYPERLINK = b'<Relationships><Relationship Id="9" Type="http://x/hyperlink" Target="https://insurer.in" TargetMode="External"/></Relationships>'


@pytest.fixture(autouse=True)
def _no_upload_limit(monkeypatch):
    monkeypatch.setenv("CLAIMLENS_UPLOAD_RATE", "1000/minute")


PDF_BASE = pdf_bytes("hello " * 60)


def test_malicious_and_disguised_files_rejected():
    c = client()
    cases = {
        "evil.pdf": b"MZ" + b"This program cannot be run in DOS mode. " * 20,
        "a.txt": b"\x7fELF" + b"\x01" * 500,
        "script.txt": b"#!/bin/sh\nrm -rf /\n" + b"x" * 300,
        "page.txt": b"<html><script>alert(1)</script></html>" + b"policy " * 60,
        "ps.txt": b"powershell -enc AAAA " + b"policy text " * 40,
        "rich.rtf": b"{\\rtf1 " + b"policy " * 60,
        "bin.txt": bytes(range(1, 255)) * 20,
        "jar.docx": _zip({"META-INF/MANIFEST.MF": "x", "A.class": "y" * 300}),
        "macro.docx": _docx_with({"word/vbaProject.bin": b"\x00" * 50}),
        "macro2.docx": _docx_with(ctypes="<!-- macroEnabled -->"),
        "ole.docx": _docx_with({"word/embeddings/oleObject1.bin": b"x"}),
        "dropper.docx": _docx_with({"word/payload.exe": b"MZ"}),
        "tmpl.docx": _docx_with({"word/_rels/settings.xml.rels": EXTERNAL}),
        "js.pdf": PDF_BASE.replace(b"/Type/Catalog", b"/Type/Catalog/OpenAction<</S/JavaScript/JS(app.alert(1))>>"),
        "obf.pdf": PDF_BASE.replace(b"/Type/Catalog", b"/Type/Catalog/OpenAction<</S/J#61vaScript/JS(x)>>"),
        "launch.pdf": PDF_BASE.replace(b"/Type/Catalog", b"/Type/Catalog/AA<</O<</S/Launch/F(cmd.exe)>>>>"),
    }
    for name, data in cases.items():
        r = up(c, name, data)
        assert r.status_code == 422, (name, r.status_code, r.text)


def test_clean_variants_accepted():
    c = client()
    assert up(c, "link.docx", _docx_with({"word/_rels/document.xml.rels": HYPERLINK})).status_code == 200
    assert up(c, "win.txt", ("Sum insured is Rs. 5,00,000 “per year”. " * 20).encode("cp1252")).status_code == 200
    assert up(c, "ok.md", POLICY.encode("utf-8-sig")).status_code == 200
    assert up(c, "p.pdf", pdf_bytes("Sum insured is Rs. 5,00,000 per year. " * 10)).status_code == 200


def test_rate_limit_keys_on_forwarded_ip_only_when_trusted(monkeypatch):
    llm = MockLLM(Facts(procedure="x", diagnosis="x", pre_existing=False, coverage_months=0), Rules(sum_insured=Cited(value=D(1), clause_id="x")))
    monkeypatch.setenv("CLAIMLENS_UPLOAD_RATE", "1/minute")

    def post(c, ip):
        return c.post("/policies/upload", files={"file": ("a.txt", POLICY.encode())}, headers={"x-forwarded-for": ip}).status_code

    monkeypatch.setenv("CLAIMLENS_TRUST_PROXY", "1")
    c = TestClient(create_app(llm), raise_server_exceptions=False)
    assert post(c, "1.1.1.1") == 200 and post(c, "2.2.2.2") == 200 and post(c, "1.1.1.1") == 429
    monkeypatch.delenv("CLAIMLENS_TRUST_PROXY")  # untrusted: spoofed header must not dodge the limit
    c = TestClient(create_app(llm), raise_server_exceptions=False)
    assert post(c, "3.3.3.3") == 200 and post(c, "4.4.4.4") == 429
