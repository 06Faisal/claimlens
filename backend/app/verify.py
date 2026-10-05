"""Citation verification in code: every cited clause must be one we retrieved, and the extracted value
must literally appear in that clause. Returns a list of problems; empty means verified."""
import re
from decimal import Decimal

from .engine import Facts
from .ingest import Clause
from .schemas import Rules

_NUM = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(lakhs?|crores?)?", re.I)
_MULT = {"lakh": 100000, "lakhs": 100000, "crore": 10000000, "crores": 10000000}


def numbers_in(text: str) -> set[Decimal]:
    out = set()
    for m in _NUM.finditer(text):
        try:
            v = Decimal(m.group(1).replace(",", ""))
        except Exception:
            continue
        if m.group(2):
            v *= _MULT[m.group(2).lower()]
        out.add(v)
        # Indian grouping "5,00,000" and plain digits both reduce to the same Decimal above
    return out


def verify(rules: Rules, facts: Facts, clauses: dict[str, Clause]) -> list[str]:
    problems = []

    def clause(cid):
        c = clauses.get(cid)
        if not c:
            problems.append(f"cited clause {cid!r} was not retrieved")
        return c

    for name in ("sum_insured", "room_rent_cap_per_day", "copay_percent", "deductible",
                 "waiting_period_months", "ped_waiting_months", "irdai_ped_cap_months"):
        f = getattr(rules, name)
        if f is None:
            continue
        c = clause(f.clause_id)
        if c and f.value not in numbers_in(c.text):
            problems.append(f"{name}={f.value} not stated in {f.clause_id}")
        if c and name == "irdai_ped_cap_months" and c.kind != "irdai":
            problems.append("irdai cap must cite an IRDAI clause")
        if c and name != "irdai_ped_cap_months" and c.kind != "policy":
            problems.append(f"{name} must cite a policy clause")

    case_text = f"{facts.procedure} {facts.diagnosis}".lower()
    if rules.exclusion:
        c = clause(rules.exclusion.clause_id)
        t = rules.exclusion.term.lower()
        if c and t not in c.text.lower():
            problems.append(f"exclusion term {t!r} not in {c.id}")
        if t not in case_text:
            problems.append(f"exclusion term {t!r} not in the claim facts")
    if rules.sub_limit:
        sl = rules.sub_limit
        c = clause(sl.clause_id)
        t = sl.procedure.lower()
        if c and (t not in c.text.lower() or sl.amount not in numbers_in(c.text)):
            problems.append(f"sub-limit {t!r}={sl.amount} not stated in {sl.clause_id}")
        if t not in case_text:
            problems.append(f"sub-limit procedure {t!r} not in the claim facts")
    return problems
