"""Deterministic payout engine. No LLM here: money is computed in Decimal, in the policy's deduction order."""
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from pydantic import BaseModel, Field

from .schemas import Outcome, Rules, Step

# IRDAI: pharmacy, consumables, implants, devices and diagnostics are excluded from proportionate deduction.
PROP_DEDUCTION_CLAUSE = "irdai-2020-prop-deduction:1"
ZERO = Decimal(0)


class Bill(BaseModel):
    room_rent_per_day: Decimal
    room_days: int
    associated: Decimal
    other: Decimal
    non_payable: Decimal = ZERO


class Facts(BaseModel):
    procedure: str = Field(max_length=120)
    diagnosis: str = Field(max_length=120)
    pre_existing: Optional[bool]
    coverage_months: int


def _q(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def compute(rules: Rules, facts: Facts, bill: Bill) -> Optional[Outcome]:
    """Return None when the case cannot be decided (caller abstains)."""
    room = bill.room_rent_per_day * bill.room_days
    claimed = room + bill.associated + bill.other + bill.non_payable
    steps: list[Step] = []
    cites: list[str] = []

    def reject(name, note, ids):
        steps.append(Step(name=name, amount_after=ZERO, note=note, clause_ids=ids))
        return Outcome(verdict="rejected", payable=ZERO, claimed=_q(claimed), steps=steps, clause_ids=ids)

    # 1. eligibility
    if rules.exclusion:
        return reject("Eligibility", f"Excluded: {rules.exclusion.term}", [rules.exclusion.clause_id])
    waits = []  # (months, clause ids)
    if rules.waiting_period_months:
        waits.append((rules.waiting_period_months.value, [rules.waiting_period_months.clause_id]))
    if rules.ped_waiting_months:
        if facts.pre_existing is None:
            return None
        if facts.pre_existing:
            months, ids = rules.ped_waiting_months.value, [rules.ped_waiting_months.clause_id]
            cap = rules.irdai_ped_cap_months
            if cap and cap.value < months:
                months, ids = cap.value, [cap.clause_id]
            waits.append((months, ids))
    for months, ids in waits:
        if facts.coverage_months < months:
            return reject("Eligibility", f"Waiting period {months} months not completed ({facts.coverage_months} months covered)", ids)
    steps.append(Step(name="Eligibility", amount_after=_q(claimed), note="No exclusion or unmet waiting period",
                      clause_ids=[i for _, ids in waits for i in ids]))

    # 2. non-payables
    payable = claimed - bill.non_payable
    steps.append(Step(name="Non-payables", amount_after=_q(payable), note=f"Removed {_q(bill.non_payable)} never covered"))

    # 3. room rent: proportionate deduction on room + associated costs only
    cap = rules.room_rent_cap_per_day
    if cap and bill.room_rent_per_day > cap.value:
        ratio = cap.value / bill.room_rent_per_day
        payable -= (room + bill.associated) * (1 - ratio)
        ids = [cap.clause_id, PROP_DEDUCTION_CLAUSE]
        cites += ids
        steps.append(Step(name="Room rent", amount_after=_q(payable), clause_ids=ids,
                          note=f"Cap {cap.value}/day vs room {bill.room_rent_per_day}/day: {_q(ratio * 100)}% on room and associated charges only"))
    else:
        steps.append(Step(name="Room rent", amount_after=_q(payable), note="Within cap or no cap"))

    # 4. sub-limit
    sl = rules.sub_limit
    if sl and payable > sl.amount:
        payable = sl.amount
        cites.append(sl.clause_id)
        steps.append(Step(name="Sub-limit", amount_after=_q(payable), note=f"Capped at {sl.amount} for {sl.procedure}", clause_ids=[sl.clause_id]))
    else:
        steps.append(Step(name="Sub-limit", amount_after=_q(payable), note="Not applicable"))

    # 5. deductible, then co-pay
    ids = []
    if rules.deductible:
        payable = max(ZERO, payable - rules.deductible.value)
        ids.append(rules.deductible.clause_id)
    if rules.copay_percent:
        payable -= payable * rules.copay_percent.value / 100
        ids.append(rules.copay_percent.clause_id)
    cites += ids
    steps.append(Step(name="Deductible and co-pay", amount_after=_q(payable), note="Deductible first, then co-payment", clause_ids=ids))

    # 6. sum insured
    if payable > rules.sum_insured.value:
        payable = rules.sum_insured.value
        cites.append(rules.sum_insured.clause_id)
    steps.append(Step(name="Sum insured", amount_after=_q(payable), note=f"Cap {rules.sum_insured.value}", clause_ids=[rules.sum_insured.clause_id]))

    payable = _q(max(ZERO, payable))
    verdict = "rejected" if payable <= 0 else "payable" if payable >= _q(claimed) else "partially_payable"
    all_ids = list(dict.fromkeys(cites + [i for s in steps for i in s.clause_ids]))
    return Outcome(verdict=verdict, payable=payable, claimed=_q(claimed), steps=steps, clause_ids=all_ids)
