import os
from pathlib import Path

# Load KEY=VALUE lines from backend/.env or claimlens/.env (both gitignored) without overriding real environment variables.
for _env in (Path(__file__).resolve().parents[1] / ".env", Path(__file__).resolve().parents[2] / ".env"):
    if _env.is_file():
        for _l in _env.read_text(encoding="utf-8").splitlines():
            _k, _, _v = _l.partition("=")
            if _k.strip() and not _k.lstrip().startswith("#") and _v.strip():
                os.environ.setdefault(_k.strip(), _v.strip().strip("\"'"))
