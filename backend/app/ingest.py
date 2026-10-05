"""Load clause corpus from data/*.md. Clause ids keep the policy's own section numbers."""
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
_HEAD = re.compile(r"^# ([a-z0-9-]+) \| (.+) \| effective: (\d{4}-\d{2}-\d{2}) \| kind: (policy|irdai)$")
_SEC = re.compile(r"^## (\S+) \| (.+)$")


@dataclass(frozen=True)
class Clause:
    id: str
    doc_id: str
    kind: str
    effective: date
    title: str
    text: str


def load_corpus(root: Path = DATA) -> dict[str, Clause]:
    out: dict[str, Clause] = {}
    for f in sorted(root.glob("*/*.md")):
        lines = f.read_text(encoding="utf-8").splitlines()
        m = _HEAD.match(lines[0])
        if not m:
            raise ValueError(f"bad header in {f.name}")
        doc, doc_title, eff, kind = m.groups()
        cur, buf = None, []

        def flush():
            if cur:
                cid = f"{doc}:{cur[0]}"
                out[cid] = Clause(cid, doc, kind, date.fromisoformat(eff), f"{doc_title}, {cur[0]} {cur[1]}", " ".join(buf).strip())

        for ln in lines[1:]:
            s = _SEC.match(ln)
            if s:
                flush()
                cur, buf = s.groups(), []
            elif ln.strip():
                buf.append(ln.strip())
        flush()
    return out


def policy_ids(corpus: dict[str, Clause]) -> set[str]:
    return {c.doc_id for c in corpus.values() if c.kind == "policy"}
