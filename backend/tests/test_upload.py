import io
from decimal import Decimal as D

from docx import Document
from fastapi.testclient import TestClient

from app.engine import Facts
from app.main import create_app
from app.mock_llm import MockLLM
from app.schemas import Cited, Rules

POLICY = ("Sum Insured\nThe Sum Insured under this policy is Rs. 3,00,000 per policy year.\n\n"
          "Room Rent\nRoom rent is limited to Rs. 4,000 per day for every hospital stay under this policy.\n\n"
          "Exclusions\nCosmetic surgery is not covered under any circumstances whatsoever for the insured person.\n") * 2


def pdf_bytes(text: str) -> bytes:
    stream = f"BT /F1 10 Tf 10 780 Td ({text}) Tj ET".encode()
    objs = [b"<</Type/Catalog/Pages 2 0 R>>", b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
            b"<</Length %d>>stream\n" % len(stream) + stream + b"\nendstream", b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs)
    return out + b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, x)


def client():
    llm = MockLLM(Facts(procedure="x", diagnosis="x", pre_existing=False, coverage_months=0),
                  Rules(sum_insured=Cited(value=D(300000), clause_id="s1")))
    return TestClient(create_app(llm), raise_server_exceptions=False)


def up(c, name, data):
    return c.post("/policies/upload", files={"file": (name, data)})


def test_txt_upload_then_assess_uses_uploaded_clauses():
    c = client()
    r = up(c, "My Policy.txt", POLICY.encode())
    assert r.status_code == 200 and r.json()["name"] == "My Policy"
    pid = r.json()["policy_id"]
    # mock cites a clause id that does not exist in the upload -> must abstain, proving uploads reach the verifier
    body = dict(policy_id=pid, description="knee surgery", policy_start_date="2024-04-01", claim_date="2025-06-01",
                continuous_coverage_months=60, room_rent_per_day=1000, room_days=2, associated_charges=1000, other_charges=1000,
                non_payable_charges=0)
    assert c.post("/assess", json=body).json()["status"] == "abstained"


def test_docx_and_pdf_upload():
    c = client()
    d = Document()
    d.add_paragraph(POLICY)
    buf = io.BytesIO(); d.save(buf)
    assert up(c, "p.docx", buf.getvalue()).status_code == 200
    assert up(c, "p.pdf", pdf_bytes("Sum insured is Rs. 5,00,000 per year. " * 10)).status_code == 200


def test_rejects_junk_big_and_empty():
    c = client()
    assert up(c, "a.exe", b"MZ\x90\x00\x00" + bytes(3000)).status_code == 422
    assert up(c, "a.txt", b"too short").status_code == 422
    assert up(c, "a.pdf", b"%PDF-1.4 garbage").status_code == 422
    assert up(c, "a.txt", b"x" * (5 * 1024 * 1024 + 200_000)).status_code == 413


def test_samples_have_names():
    assert any(s["name"].startswith("StarCare") for s in client().get("/samples").json())
