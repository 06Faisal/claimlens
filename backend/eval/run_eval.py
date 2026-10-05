"""Hand-solved scenarios. Default: MockLLM with oracle rule extraction (tests engine, verifier, retrieval scope).
--live: real Claude extraction (needs ANTHROPIC_API_KEY); measures end-to-end accuracy and abstain rate."""
import json
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.engine import Facts  # noqa: E402
from app.ingest import load_corpus  # noqa: E402
from app.mock_llm import MockLLM  # noqa: E402
from app.pipeline import assess  # noqa: E402
from app.schemas import AssessRequest, Rules  # noqa: E402


def main(live: bool) -> int:
    corpus = load_corpus()
    scenarios = json.loads((Path(__file__).parent / "scenarios.json").read_text())
    if live:
        from app.llm import make_llm
        llm = make_llm()
    ok = abstained = 0
    for s in scenarios:
        if live:
            time.sleep(float(os.environ.get("CLAIMLENS_EVAL_SLEEP", "0")))  # free-tier quotas
        model = llm if live else MockLLM(Facts(**s["facts"]), Rules(**s["rules"]))
        req = dict(s["request"])
        if s["facts"].get("pre_existing") is False:
            req.setdefault("pre_existing", False)  # what a user ticks in the UI; live model otherwise abstains when it is unstated
        a = assess(AssessRequest(**req), corpus, model)
        e = s["expected"]
        good = a.status == e["status"] and (a.status == "abstained" or (
            a.verdict == e["verdict"] and a.payable == Decimal(e["payable"])))
        ok += good
        abstained += a.status == "abstained"
        print(f"{'PASS' if good else 'FAIL'}  {s['name']}: {a.status} {a.verdict} {a.payable}")
    n = len(scenarios)
    print(f"\naccuracy {ok}/{n}  abstain rate {abstained}/{n}  mode={'live' if live else 'mock'}")
    return 0 if ok == n else 1


if __name__ == "__main__":
    sys.exit(main("--live" in sys.argv))
