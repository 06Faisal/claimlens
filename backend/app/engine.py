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
    payable = claimed - bill.non_payable
    steps.append(Step(name="Non-payables", amount_after=_q(payable), note=(f"{_inr(bill.non_payable)} of the bill is for items no policy pays for." if bill.non_payable else "Nothing on your bill is in this group.")))

    # 3. room rent: proportionate deduction on room + associated costs only
    cap = rules.room_rent_cap_per_day
    if cap and bill.room_rent_per_day > cap.value:
        ratio = cap.value / bill.room_rent_per_day
        payable -= (room + bill.associated) * (1 - ratio)
        ids = [cap.clause_id, PROP_DEDUCTION_CLAUSE]
        cites += ids
        steps.append(Step(name="Room rent", amount_after=_q(payable), clause_ids=ids,
                          note=(f"Your policy allows {_inr(cap.value)} a day but the room cost {_inr(bill.room_rent_per_day)} a day, so room, doctor and nursing charges are paid at {_q(ratio * 100).normalize():f}%. "
                                f"Medicines, implants and tests are not reduced.")))
    else:
        steps.append(Step(name="Room rent", amount_after=_q(payable), note="Your room is within the policy's daily limit."))

    # 4. sub-limit
    sl = rules.sub_limit
    if sl and payable > sl.amount:
        payable = sl.amount
        cites.append(sl.clause_id)
        steps.append(Step(name="Sub-limit", amount_after=_q(payable), note=f"Your policy pays at most {_inr(sl.amount)} for {sl.procedure}.", clause_ids=[sl.clause_id]))
    else:
        steps.append(Step(name="Sub-limit", amount_after=_q(payable), note="No special limit applies to this treatment."))

    # 5. deductible, then co-pay
    ids, parts, start = [], [], payable
    if rules.deductible:
        taken = min(payable, rules.deductible.value)
        payable -= taken
        ids.append(rules.deductible.clause_id)
        parts.append(f"The first {_inr(taken)} of the claim is yours to pay. Your policy calls this the deductible.")
    if rules.copay_percent:
        share = payable * rules.copay_percent.value / 100
        payable -= share
        ids.append(rules.copay_percent.clause_id)
        parts.append(f"Of the {_inr(payable + share)} left, you also pay {rules.copay_percent.value:f}% ({_inr(share)}), called the co-payment. The insurer pays the rest.")
    if parts:
        parts.append(f"Together, you pay {_inr(start - payable)} here.")
    cites += ids
    steps.append(Step(name="Deductible and co-pay", amount_after=_q(payable), note=" ".join(parts) or "Your policy has no deductible or co-payment.", clause_ids=ids))

    # 6. sum insured
    capped = payable > rules.sum_insured.value
    if capped:
        payable = rules.sum_insured.value
        cites.append(rules.sum_insured.clause_id)
    steps.append(Step(name="Sum insured", amount_after=_q(payable), note=(f"The most your policy pays in a year is {_inr(rules.sum_insured.value)}, so the amount is capped." if capped else f"Within the most your policy pays in a year ({_inr(rules.sum_insured.value)})."), clause_ids=[rules.sum_insured.clause_id]))

    payable = _q(max(ZERO, payable))
    # reached only when nothing excluded the claim: a zero here means the bill is within the deductible, not "not covered"
    verdict = "payable" if payable >= _q(claimed) else "partially_payable"
    all_ids = list(dict.fromkeys(cites + [i for s in steps for i in s.clause_ids]))
    return Outcome(verdict=verdict, payable=payable, claimed=_q(claimed), steps=steps, clause_ids=all_ids)
