"""Deterministic stand-in for the LLM: tests and the offline eval use canned facts/rules."""
from .engine import Facts
from .ingest import Clause
from .schemas import Outcome, Rules


class MockLLM:
    def __init__(self, facts: Facts, rules: Rules, explanation: str = ""):
        self.facts, self.rules, self.explanation = facts, rules, explanation

    def structure(self, description: str) -> Facts:
        return self.facts

    def extract_rules(self, facts: Facts, clauses: list[Clause]) -> Rules:
        return self.rules

    def explain(self, outcome: Outcome, clauses: list[Clause]) -> str:
        return self.explanation
