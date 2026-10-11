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


def _inr(x) -> str:
    """Rupees in Indian grouping (3,00,000), no paise when whole."""
    x = _q(Decimal(x))
    whole, frac = divmod(abs(x), 1)
    d = str(int(whole))
    d = d if len(d) <= 3 else (",".join([d[:-3][::-1][i:i + 2][::-1] for i in range(0, len(d[:-3]), 2)][::-1]) + "," + d[-3:])
    return "₹" + d + (f".{int(frac * 100):02d}" if frac else "")


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
        return reject("Eligibility", f"Your policy excludes this: {rules.exclusion.term.lower()}.", [rules.exclusion.clause_id])
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
            return reject("Eligibility", f"Your policy covers this only after {months} months, and you have completed {facts.coverage_months}.", ids)
    steps.append(Step(name="Eligibility", amount_after=_q(claimed), note="Nothing in your policy rules this treatment out, and any waiting period is complete.",
                      clause_ids=[i for _, ids in waits for i in ids]))

    # 2. non-payables
    before = claimed
    payable = claimed - bill.non_payable
    steps.append(Step(name="Non-payables", amount_after=_q(payable),
                      note=("Some items on the bill are never paid by any policy." if bill.non_payable else "Nothing on your bill is in this group."),
                      calc=[f"{_inr(before)} - {_inr(bill.non_payable)} = {_inr(payable)}"] if bill.non_payable else []))

    # 3. room rent: proportionate deduction on room + associated costs only
    before = payable
    cap = rules.room_rent_cap_per_day
    if cap and bill.room_rent_per_day > cap.value:
        ratio = cap.value / bill.room_rent_per_day
        pct = _q(ratio * 100).normalize()
        base = room + bill.associated
        cut = base * (1 - ratio)
        payable -= cut
        ids = [cap.clause_id, PROP_DEDUCTION_CLAUSE]
        cites += ids
        steps.append(Step(name="Room rent", amount_after=_q(payable), clause_ids=ids,
                          note=f"Your policy allows {_inr(cap.value)} a day but the room cost {_inr(bill.room_rent_per_day)} a day, so room, doctor and nursing charges are paid only in proportion. "
                               f"Medicines, implants and tests are not reduced.",
                          calc=[f"Room: {_inr(bill.room_rent_per_day)} x {bill.room_days} days = {_inr(room)}",
                                f"Room + doctor and nursing fees: {_inr(room)} + {_inr(bill.associated)} = {_inr(base)}",
                                f"Share your policy pays: {_inr(cap.value)} / {_inr(bill.room_rent_per_day)} = {pct:f}%",
                                f"Paid: {_inr(base)} x {pct:f}% = {_inr(base - cut)}",
                                f"Not paid: {_inr(base)} - {_inr(base - cut)} = {_inr(cut)}",
                                f"Medicines, implants and tests ({_inr(bill.other)}): paid in full",
                                f"Bill after this step: {_inr(before)} - {_inr(cut)} = {_inr(payable)}"]))
    else:
        steps.append(Step(name="Room rent", amount_after=_q(payable), note="Your room is within the policy's daily limit, so nothing is reduced."))

    # 4. sub-limit
    sl = rules.sub_limit
    if sl and payable > sl.amount:
        before = payable
        payable = sl.amount
        cites.append(sl.clause_id)
        steps.append(Step(name="Sub-limit", amount_after=_q(payable), note=f"Your policy pays at most {_inr(sl.amount)} for {sl.procedure}.", clause_ids=[sl.clause_id],
                          calc=[f"{_inr(before)} is more than the limit, so the insurer pays {_inr(sl.amount)}"]))
    else:
        steps.append(Step(name="Sub-limit", amount_after=_q(payable), note="No special limit applies to this treatment."))

    # 5. deductible, then co-pay
    ids, calc, start = [], [], payable
    if rules.deductible:
        taken = min(payable, rules.deductible.value)
        payable -= taken
        ids.append(rules.deductible.clause_id)
        calc.append(f"Deductible (you pay first): {_inr(start)} - {_inr(taken)} = {_inr(payable)} left")
    left = payable
    if rules.copay_percent:
        share = left * rules.copay_percent.value / 100
        payable -= share
        ids.append(rules.copay_percent.clause_id)
        calc.append(f"Co-payment (your % of what is left): {rules.copay_percent.value:f}% of {_inr(left)} = {_inr(share)}")
        calc.append(f"Insurer pays: {_inr(left)} - {_inr(share)} = {_inr(payable)}")
    if calc:
        calc.append(f"You pay in this step: {_inr(start)} - {_inr(payable)} = {_inr(start - payable)}")
    cites += ids
    steps.append(Step(name="Deductible and co-pay", amount_after=_q(payable),
                      note=("Your policy makes you pay a fixed amount first (the deductible), then a percentage of the rest (the co-payment)." if calc
                            else "Your policy has no deductible or co-payment."),
                      calc=calc, clause_ids=ids))

    # 6. sum insured
    capped = payable > rules.sum_insured.value
    si = rules.sum_insured.value
    if capped:
        calc = [f"{_inr(payable)} is more than the yearly maximum, so the insurer pays {_inr(si)}"]
        payable = si
        cites.append(rules.sum_insured.clause_id)
    else:
        calc = [f"{_inr(payable)} is within the yearly maximum of {_inr(si)}"]
    steps.append(Step(name="Sum insured", amount_after=_q(payable), note="The most your policy pays in one year.", calc=calc, clause_ids=[rules.sum_insured.clause_id]))

    payable = _q(max(ZERO, payable))
    # reached only when nothing excluded the claim: a zero here means the bill is within the deductible, not "not covered"
    verdict = "payable" if payable >= _q(claimed) else "partially_payable"
    all_ids = list(dict.fromkeys(cites + [i for s in steps for i in s.clause_ids]))
    return Outcome(verdict=verdict, payable=payable, claimed=_q(claimed), steps=steps, clause_ids=all_ids)
