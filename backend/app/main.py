import logging
import os
import re
from typing import Optional

import hashlib

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from .ingest import load_corpus, policy_ids
from .llm import LLM
from .pipeline import assess
from .schemas import Assessment, AssessRequest
from .upload import MAX_FILE, UploadError, clauses_from_chunks, extract_text, to_chunks

MAX_BODY = 16 * 1024
UPLOAD_PATH = "/policies/upload"
MAX_UPLOAD_BODY = MAX_FILE + 64 * 1024  # multipart overhead
MAX_ASSESS_UPLOAD_BODY = 1024 * 1024  # /assess carrying uploaded policy chunks
log = logging.getLogger("claimlens")

HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"no-store"),
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
]


class SecurityMiddleware:
    """Pure ASGI: caps request body bytes (also for chunked uploads) and adds security headers."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        size = 0
        cap = {UPLOAD_PATH: MAX_UPLOAD_BODY, "/assess": MAX_ASSESS_UPLOAD_BODY}.get(scope["path"], MAX_BODY)

        async def limited_receive():
            nonlocal size
            msg = await receive()
            if msg["type"] == "http.request":
                size += len(msg.get("body", b""))
                if size > cap:
                    return {"type": "http.request", "body": b"", "more_body": False}
            return msg

        async def send_wrapped(msg):
            if msg["type"] == "http.response.start":
                msg = {**msg, "headers": list(msg.get("headers", [])) + HEADERS}
            await send(msg)

        declared = dict(scope["headers"]).get(b"content-length")
        if declared and declared.isdigit() and int(declared) > cap:
            return await JSONResponse({"detail": "request too large"}, status_code=413)(scope, receive, send_wrapped)
        await self.app(scope, limited_receive, send_wrapped)


def create_app(llm: Optional[LLM] = None) -> FastAPI:
    debug = os.environ.get("CLAIMLENS_DEBUG") == "1"
    app = FastAPI(title="ClaimLens", docs_url="/docs" if debug else None, redoc_url=None, openapi_url="/openapi.json" if debug else None)
    corpus = load_corpus()
    valid = policy_ids(corpus)
    limiter = Limiter(key_func=get_remote_address, default_limits=[])  # ponytail: behind a proxy, key on X-Forwarded-For via trusted-proxy config
    state = {"llm": llm}

    origins = [o for o in os.environ.get("CLAIMLENS_CORS_ORIGINS", "http://localhost:3000").split(",") if o]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"], allow_headers=["content-type"])
    app.add_middleware(SecurityMiddleware)

    @app.exception_handler(RateLimitExceeded)
    async def _rl(_, __):
        return JSONResponse({"detail": "rate limit exceeded"}, status_code=429)

    @app.exception_handler(Exception)
    async def _err(_, exc):
        log.error("unhandled %s", type(exc).__name__)
        return JSONResponse({"detail": "internal error"}, status_code=500)

    def get_llm() -> LLM:
        if state["llm"] is None:
            from .llm import make_llm
            try:
                state["llm"] = make_llm()
            except Exception:
                raise HTTPException(503, "LLM not configured")
        return state["llm"]

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/policies")
    def policies():
        return sorted(valid)

    @app.get("/samples")
    def samples():
        names = {c.doc_id: c.title.rsplit(", ", 1)[0] for c in corpus.values() if c.kind == "policy"}
        return [{"id": i, "name": names[i]} for i in sorted(valid)]

    @app.post(UPLOAD_PATH)
    @limiter.limit(os.environ.get("CLAIMLENS_UPLOAD_RATE", "6/minute"))
    async def upload_policy(request: Request, file: UploadFile = File(...)):
        data = await file.read(MAX_FILE + 1)
        if len(data) > MAX_FILE:
            raise HTTPException(413, "That file is larger than 5 MB.")
        try:
            text = extract_text(data)
        except UploadError as e:
            raise HTTPException(422, str(e))
        pid = "upload-" + hashlib.sha256(text.encode()).hexdigest()[:12]
        name = re.sub(r"[^\w .()-]", "", (file.filename or "Your policy").rsplit(".", 1)[0])[:60].strip() or "Your policy"
        return {"policy_id": pid, "name": name, "chunks": to_chunks(text)}  # nothing stored server-side

    @app.post("/assess", response_model=Assessment)
    @limiter.limit(os.environ.get("CLAIMLENS_GLOBAL_RATE", "300/hour"), key_func=lambda: "global")  # caps total LLM spend
    @limiter.limit(os.environ.get("CLAIMLENS_RATE", "10/minute"))
    def assess_endpoint(request: Request, req: AssessRequest):
        if req.policy_chunks is not None:
            return assess(req, {**corpus, **clauses_from_chunks(req.policy_chunks, req.policy_id)}, get_llm())
        if req.policy_id not in valid:
            raise HTTPException(422, "unknown policy_id")
        return assess(req, corpus, get_llm())

    app.state.limiter = limiter
    return app


app = create_app()
