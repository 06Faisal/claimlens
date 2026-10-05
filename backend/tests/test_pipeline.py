from datetime import date
from decimal import Decimal as D

import pytest

from app.engine import Facts
from app.ingest import load_corpus
from app.mock_llm import MockLLM
from app.pipeline import assess
from app.retrieve import retrieve
from app.schemas import AssessRequest, Cited, Rules
from app.verify import numbers_in

CORPUS = load_corpus()


def req(**kw):
    base = dict(policy_id="starcare-gold", description="knee replacement", policy_start_date="2024-04-01", claim_date="2025-06-01",
                continuous_coverage_months=60, room_rent_per_day=8000, room_days=5, associated_charges=120000,
                other_charges=160000, non_payable_charges=0)
    return AssessRequest(**{**base, **kw})


def facts(**kw):
    return Facts(**{**dict(procedure="knee replacement", diagnosis="osteoarthritis", pre_existing=False, coverage_months=0), **kw})


def rules(**kw):
    base = dict(sum_insured=Cited(value=D(500000), clause_id="starcare-gold:2.1"),
                room_rent_cap_per_day=Cited(value=D(5000), clause_id="starcare-gold:3.1"))
    return Rules(**{**base, **kw})


def test_paper_example_end_to_end():
    a = assess(req(), CORPUS, MockLLM(facts(), rules()))
    assert a.status == "answered" and a.payable == D("260000") and a.verdict == "partially_payable"
    ids = {c["clause_id"] for c in a.citations}
    assert {"starcare-gold:3.1", "irdai-2020-prop-deduction:1"} <= ids


def test_hallucinated_clause_id_abstains():
    bad = rules(room_rent_cap_per_day=Cited(value=D(5000), clause_id="starcare-gold:9.9"))
    assert assess(req(), CORPUS, MockLLM(facts(), bad)).status == "abstained"


def test_value_not_in_clause_abstains():
    bad = rules(room_rent_cap_per_day=Cited(value=D(7000), clause_id="starcare-gold:3.1"))
    assert assess(req(), CORPUS, MockLLM(facts(), bad)).status == "abstained"


def test_unsupported_feature_abstains():
    assert assess(req(), CORPUS, MockLLM(facts(), rules(unsupported=["restoration_benefit"]))).status == "abstained"


def test_irdai_rule_not_visible_before_effective_date():
    cap = Cited(value=D(36), clause_id="irdai-2024-master:1")
    r = rules(ped_waiting_months=Cited(value=D(48), clause_id="starcare-gold:4.3"), irdai_ped_cap_months=cap)
    ok = assess(req(policy_start_date="2024-04-01", continuous_coverage_months=40), CORPUS, MockLLM(facts(pre_existing=True), r))
    assert ok.status == "answered" and ok.payable > 0
    old = assess(req(policy_start_date="2023-01-01", continuous_coverage_months=40), CORPUS, MockLLM(facts(pre_existing=True), r))
    assert old.status == "abstained"  # circular not in force -> cite not retrievable


def test_explanation_with_invented_amount_is_replaced():
    a = assess(req(), CORPUS, MockLLM(facts(), rules(), "You will receive Rs. 999,999 [starcare-gold:3.1]"))
    assert "999" not in a.explanation


def test_explanation_with_unknown_citation_is_replaced():
    a = assess(req(), CORPUS, MockLLM(facts(), rules(), "Fine [fake-doc:1]"))
    assert "fake-doc" not in a.explanation


def test_llm_failure_abstains_without_leaking():
    class Boom(MockLLM):
        def structure(self, d):
            raise RuntimeError("secret-key-sk-123")
    a = assess(req(), CORPUS, Boom(facts(), rules()))
    assert a.status == "abstained" and "sk-123" not in a.reason


def test_retrieval_scoped_to_policy():
    got = retrieve(CORPUS, "starcare-gold", date(2025, 1, 1))
    assert got and all(c.doc_id == "starcare-gold" or c.kind == "irdai" for c in got)


def test_numbers_in_indian_grouping_and_lakh():
    assert D(500000) in numbers_in("Rs. 5,00,000 per year") and D(300000) in numbers_in("3 lakh") and D(10) in numbers_in("10%")


@pytest.mark.parametrize("bad", [dict(room_days=-1), dict(other_charges=-5), dict(policy_id="../etc/passwd"),
                                 dict(description="x" * 2001), dict(claim_date="2020-01-01"), dict(other_charges="NaN"), dict(extra=1)])
def test_request_validation(bad):
    with pytest.raises(Exception):
        req(**bad)
