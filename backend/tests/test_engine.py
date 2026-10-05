from decimal import Decimal as D

from app.engine import Bill, Facts, compute
from app.schemas import Cited, Exclusion, Rules, SubLimit


def cited(v, cid="P:1"):
    return Cited(value=D(str(v)), clause_id=cid)


def bill(room_rent=8000, days=5, assoc=120000, other=160000, nonpay=0):
    return Bill(room_rent_per_day=D(room_rent), room_days=days, associated=D(assoc), other=D(other), non_payable=D(nonpay))


def facts(months=60, ped=False):
    return Facts(procedure="knee replacement", diagnosis="osteoarthritis", pre_existing=ped, coverage_months=months)


def test_paper_example_room_rent_applies_only_to_associated():
    # cap 5000, room 8000 x5d = 40k + 120k assoc = 160k group; ratio .625 -> 100k; + 160k other = 260k
    r = compute(Rules(sum_insured=cited(500000), room_rent_cap_per_day=cited(5000)), facts(), bill())
    assert r.payable == D("260000") and r.verdict == "partially_payable"


def test_no_room_deduction_when_within_cap():
    r = compute(Rules(sum_insured=cited(500000), room_rent_cap_per_day=cited(9000)), facts(), bill())
    assert r.payable == D("320000") and r.verdict == "payable"


def test_order_subl_then_deductible_then_copay_then_sum_insured():
    rules = Rules(sum_insured=cited(100000), sub_limit=SubLimit(procedure="knee", amount=D("200000"), clause_id="P:2"),
                  deductible=cited(10000), copay_percent=cited(10))
    r = compute(rules, facts(), bill(room_rent=0, days=0, assoc=0, other=300000))
    # 300k -> sublimit 200k -> -10k = 190k -> -10% = 171k -> SI cap 100k
    assert r.payable == D("100000")


def test_copay_after_deductible():
    r = compute(Rules(sum_insured=cited(500000), deductible=cited(10000), copay_percent=cited(10)), facts(), bill(room_rent=0, days=0, assoc=0, other=110000))
    assert r.payable == D("90000")


def test_non_payables_removed():
    r = compute(Rules(sum_insured=cited(500000)), facts(), bill(room_rent=0, days=0, assoc=0, other=100000, nonpay=5000))
    assert r.payable == D("100000")


def test_exclusion_rejects():
    r = compute(Rules(sum_insured=cited(500000), exclusion=Exclusion(term="knee", clause_id="P:3")), facts(), bill())
    assert r.verdict == "rejected" and r.payable == 0


def test_waiting_period_rejects():
    r = compute(Rules(sum_insured=cited(500000), waiting_period_months=cited(24)), facts(months=12), bill())
    assert r.verdict == "rejected"


def test_irdai_caps_ped_waiting():
    rules = Rules(sum_insured=cited(500000), ped_waiting_months=cited(48), irdai_ped_cap_months=cited(36, "I:1"))
    assert compute(rules, facts(months=40, ped=True), bill()).verdict != "rejected"
    assert compute(rules, facts(months=30, ped=True), bill()).verdict == "rejected"


def test_unknown_ped_status_abstains():
    rules = Rules(sum_insured=cited(500000), ped_waiting_months=cited(48))
    assert compute(rules, facts(months=10, ped=None), bill()) is None


def test_payable_never_negative_or_above_claim():
    r = compute(Rules(sum_insured=cited(500000), deductible=cited(999999)), facts(), bill())
    assert r.payable == 0 and r.verdict == "rejected"
