"""Bugs found in the final live review."""
from datetime import date
from decimal import Decimal as D

from app.engine import Facts
from app.ingest import load_corpus
from app.mock_llm import MockLLM
from app.pipeline import assess
from app.schemas import AssessRequest, Cited, Exclusion, Rules
from app.verify import term_in_case, verify

CORPUS = load_corpus()


def req(**kw):
    base = dict(policy_id="securehealth-basic", description="Appendectomy", policy_start_date=date(2024, 4, 1), claim_date=date(2025, 6, 1),
                continuous_coverage_months=60, pre_existing=False, room_rent_per_day=1000, room_days=2, associated_charges=10000,
                other_charges=20000, non_payable_charges=0)
    return AssessRequest(**{**base, **kw})


def run(r, **rules):
    llm = MockLLM(Facts(procedure="appendectomy", diagnosis="x", pre_existing=False, coverage_months=0),
                  Rules(sum_insured=Cited(value=D(300000), clause_id="securehealth-basic:2.1"), **rules))
    return assess(r, CORPUS, llm)


def test_exclusion_matches_distinctive_word_but_not_generic_ones():
    assert term_in_case("cosmetic surgery", "nose reshaping cosmetic")
    assert term_in_case("obesity treatment", "obesity")
    assert not term_in_case("obesity treatment", "knee treatment")        # "treatment" alone never matches
    assert not term_in_case("cosmetic surgery", "appendix surgery")       # "surgery" alone never matches


def test_cosmetic_claim_with_split_facts_is_now_rejected_not_abstained():
    facts = Facts(procedure="nose reshaping", diagnosis="cosmetic", pre_existing=False, coverage_months=60)
    rules = Rules(sum_insured=Cited(value=D(300000), clause_id="securehealth-basic:2.1"),
                  exclusion=Exclusion(term="Cosmetic surgery", clause_id="securehealth-basic:5.1"))
    assert verify(rules, facts, {k: v for k, v in CORPUS.items() if v.doc_id == "securehealth-basic"}) == []


def test_restoration_benefit_ignored_when_bill_fits_in_sum_insured_but_abstains_when_it_does_not():
    small = run(req(), unsupported=["restoration_benefit"])
    assert small.status == "answered"
    big = run(req(other_charges=400000), unsupported=["restoration_benefit"])
    assert big.status == "abstained" and "restoration_benefit" in big.reason


def test_package_rate_still_abstains_even_for_small_bills():
    assert run(req(), unsupported=["package_rate"]).status == "abstained"


def test_unclear_preexisting_message_tells_the_user_what_to_do():
    llm = MockLLM(Facts(procedure="x", diagnosis="y", pre_existing=None, coverage_months=0),
                  Rules(sum_insured=Cited(value=D(300000), clause_id="securehealth-basic:2.1"),
                        ped_waiting_months=Cited(value=D(48), clause_id="securehealth-basic:4.2")))
    a = assess(req(pre_existing=None), CORPUS, llm)
    assert a.status == "abstained" and "Yes or No" in a.reason
