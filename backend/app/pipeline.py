"""structure -> retrieve -> extract -> compute -> verify -> explain, or abstain with a reason."""
import logging
import re
from typing import Optional

from .engine import Bill, Facts, compute
from .ingest import Clause
from .llm import LLM, LLMError
from .retrieve import retrieve
from .schemas import Assessment, AssessRequest, Outcome
from .verify import numbers_in, verify

log = logging.getLogger("claimlens")
UNVERIFIED = "Could not verify every cited clause against the policy text."


def abstain(reason: str) -> Assessment:
    return Assessment(status="abstained", reason=reason + " Please consult the insurer or a human claims adviser.")


def _safe_explanation(text: str, outcome: Outcome) -> Optional[str]:
    """Reject LLM prose that cites unknown clauses or states amounts the engine did not produce."""
    if not set(re.findall(r"\[([a-z0-9-]+:[^\]\s]+)\]", text)) <= set(outcome.clause_ids):
        return None
    allowed = {outcome.payable, outcome.claimed} | {s.amount_after for s in outcome.steps}
    if not {n for n in numbers_in(text) if n >= 1000} <= allowed:
        return None
    return text


def _template(outcome: Outcome) -> str:
    return "Each step below shows how this amount was reached, with the policy lines it relies on."


def assess(req: AssessRequest, corpus: dict[str, Clause], llm: LLM) -> Assessment:
    try:
        facts = llm.structure(req.description)
        facts = Facts(**{**facts.model_dump(), "coverage_months": req.continuous_coverage_months})
        if req.pre_existing is not None:
            facts = Facts(**{**facts.model_dump(), "pre_existing": req.pre_existing})
        clauses = retrieve(corpus, req.policy_id, req.policy_start_date, f"{facts.procedure} {facts.diagnosis}")
        if not clauses:
            return abstain("No policy clauses found.")
        rules = llm.extract_rules(facts, clauses)
    except LLMError as e:
        return abstain(f"Could not extract rules ({e}).")
    except Exception as e:  # network, validation, SDK errors: never leak details to the client
        log.warning("llm step failed: %s", type(e).__name__)
        return abstain("The analysis service could not complete.")

    if rules.unsupported:
        return abstain(f"Policy features not yet modelled: {', '.join(rules.unsupported)}.")
    problems = verify(rules, facts, {c.id: c for c in clauses})
    if problems:
        log.info("verification failed: %s", problems)
        return abstain(UNVERIFIED)

    bill = Bill(room_rent_per_day=req.room_rent_per_day, room_days=req.room_days, associated=req.associated_charges,
                other=req.other_charges, non_payable=req.non_payable_charges)
    outcome = compute(rules, facts, bill)
    if outcome is None:
        return abstain("Pre-existing condition status is unclear from the description.")
    if any(i not in corpus for i in outcome.clause_ids):
        return abstain(UNVERIFIED)
    cite_clauses = [corpus[i] for i in outcome.clause_ids]

    try:
        text = _safe_explanation(llm.explain(outcome, cite_clauses), outcome)
    except Exception as e:
        log.warning("explain failed: %s", type(e).__name__)
        text = None
    return Assessment(status="answered", verdict=outcome.verdict, payable=outcome.payable, claimed=outcome.claimed,
                      steps=outcome.steps, explanation=text or _template(outcome),
                      citations=[{"clause_id": c.id, "title": c.title, "text": c.text} for c in cite_clauses])
