"""M8: FastAPI service wrapping the LeadGraph graph.

    uv run uvicorn api.main:app --port 8000

Keys (BYOK) are accepted per run in the request body, kept only in that run's memory and
dropped when its task ends. They are never logged, never echoed in a response and never
written to disk (run.json holds the options without keys).
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from leadgraph import config
from leadgraph.config import load_models
from leadgraph.envfile import load_env_file
from leadgraph.llm import ProviderUnavailable
from leadgraph.runner import MissingKey, NeedsApproval, RunOptions, execute, make_run_id

log = logging.getLogger("leadgraph.api")
FINAL = ("finished", "failed")


# --- request models ----------------------------------------------------------------------
class Keys(BaseModel):
    llm: str | None = Field(None, description="Anthropic/OpenAI key for the chosen provider")
    places: str | None = None
    search: str | None = None

    def __repr__(self) -> str:  # never show keys in tracebacks or logs
        return "Keys(***)"

    __str__ = __repr__


class Budget(BaseModel):
    max_places_requests: int | None = None
    max_llm_cost: float | None = None
    max_search_requests: int | None = None


class RunRequest(BaseModel):
    command: Literal["run", "discover", "enrich"] = "discover"
    city: str = Field("berlin", min_length=1, max_length=80,
                      description="Any city name (looked up on Google Places) or a config id")
    segment: str = "hausverwaltung"
    area: str | None = None
    provider: str = "anthropic"
    model: str | None = None
    keys: Keys = Keys()
    budget: Budget = Budget()
    limit: int | None = None
    max_rounds: int | None = None
    scout: bool = True
    sources: list[str] = []
    source_run: str | None = None
    places_tier: Literal["pro", "enterprise"] | None = None


class Approval(BaseModel):
    approved: list[int] = Field(default_factory=list, description="0-based candidate indices")
    extra: list[str] = Field(default_factory=list, description="extra list URLs")
    keys: Keys | None = Field(None, description="needed only if the server restarted")


class ResumeRequest(BaseModel):
    keys: Keys = Keys()


class ReviewDecision(BaseModel):
    decision: Literal["approve", "reject", "clear"]
    note: str | None = None


# --- run manager ---------------------------------------------------------------------------
@dataclass
class RunHandle:
    run_id: str
    opts: RunOptions  # holds keys in memory only while the run is active
    status: str = "queued"
    stage: str | None = None
    error: str | None = None
    pending: dict[str, Any] | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    task: asyncio.Task | None = None
    usage: dict[str, Any] = field(default_factory=dict)

    def publish(self, event: dict[str, Any]) -> None:
        event = {"ts": datetime.now(UTC).isoformat(timespec="seconds"), **event}
        if event.get("type") == "node_start":
            self.stage = event.get("node")
        if event.get("usage"):
            self.usage = event["usage"]
        self.events.append(event)
        for q in list(self.subscribers):
            q.put_nowait(event)

    def drop_keys(self) -> None:
        self.opts.llm_key = self.opts.places_key = self.opts.search_key = None


class RunManager:
    def __init__(self, outputs_dir: Path | None = None, patch_ctx: Any = None):
        self.outputs_dir = outputs_dir or config.OUTPUTS_DIR
        self.runs: dict[str, RunHandle] = {}
        self.patch_ctx = patch_ctx  # tests inject fake clients here

    def status_path(self, run_id: str) -> Path:
        return self.outputs_dir / run_id / "api_status.json"

    def save_status(self, h: RunHandle) -> None:
        path = self.status_path(h.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"status": h.status, "stage": h.stage, "error": h.error,
                                    "pending": h.pending}, indent=2), encoding="utf-8")

    def set_status(self, h: RunHandle, status: str, **extra: Any) -> None:
        h.status = status
        for k, v in extra.items():
            setattr(h, k, v)
        self.save_status(h)
        h.publish({"type": "status", "status": status, "error": h.error,
                   "pending": h.pending})

    def start(self, h: RunHandle, *, resume: bool = False,
              decision: dict[str, Any] | None = None) -> None:
        self.runs[h.run_id] = h
        pending = h.pending
        self.set_status(h, "running", error=None)  # visible before the task starts
        h.pending = pending
        h.task = asyncio.create_task(self._run(h, resume=resume, decision=decision))

    async def _run(self, h: RunHandle, *, resume: bool, decision: dict[str, Any] | None) -> None:
        approver = (lambda value: decision) if decision is not None else None
        try:
            await execute(h.opts, resume=resume, approver=approver, patch_ctx=self.patch_ctx,
                          events=h.publish)
            h.pending = None
            self.set_status(h, "finished")
        except NeedsApproval as exc:
            self.set_status(h, "awaiting_approval", pending=exc.args[0], error=None)
            return  # keep keys: the run continues after approval
        except MissingKey as exc:  # nothing ran: keep the run resumable
            previous = "awaiting_approval" if h.pending else ("interrupted" if resume else "failed")
            self.set_status(h, previous, error=str(exc), pending=h.pending)
            return
        except asyncio.CancelledError:
            self.set_status(h, "interrupted", error="server stopped; resume with keys")
            raise
        except ProviderUnavailable as exc:  # no credit / bad key: resumable once fixed
            self.set_status(h, "interrupted", error=str(exc))
        except Exception as exc:  # message only: provider errors never contain our keys
            log.warning("run %s failed: %s", h.run_id, type(exc).__name__)
            h.pending = None
            self.set_status(h, "failed", error=f"{type(exc).__name__}: {str(exc)[:300]}")
        h.drop_keys()
        h.publish({"type": "end", "status": h.status})

    def get(self, run_id: str) -> RunHandle:
        if run_id in self.runs:
            return self.runs[run_id]
        run_json = self.outputs_dir / run_id / "run.json"
        if not run_json.exists():
            raise HTTPException(404, f"unknown run {run_id}")
        opts = RunOptions.from_public(json.loads(run_json.read_text(encoding="utf-8")))
        st = {}
        if self.status_path(run_id).exists():
            st = json.loads(self.status_path(run_id).read_text(encoding="utf-8"))
        status = st.get("status") or ("finished" if (self.outputs_dir / run_id / "report.json")
                                      .exists() else "unknown")
        if status in ("running", "queued"):  # the server restarted while this run was active
            status = "interrupted"
        h = RunHandle(run_id=run_id, opts=opts, status=status, stage=st.get("stage"),
                      error=st.get("error"), pending=st.get("pending"))
        self.runs[run_id] = h
        return h

    async def shutdown(self) -> None:
        for h in self.runs.values():
            if h.task and not h.task.done():
                h.task.cancel()
        await asyncio.gather(*(h.task for h in self.runs.values() if h.task),
                             return_exceptions=True)


def snapshot(h: RunHandle, outputs_dir: Path) -> dict[str, Any]:
    run_dir = outputs_dir / h.run_id
    report = None
    if (run_dir / "report.json").exists():
        report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    rounds = [e["round_result"] for e in h.events if e.get("round_result")]
    usage = h.usage
    if not usage and report:
        u = report.get("usage") or {}
        usage = {"places_requests": u.get("places_requests"), "llm_calls": u.get("llm_calls"),
                 "llm_cost_usd": u.get("llm_cost_estimate_usd"),
                 "search_requests": u.get("search_requests")}
    nodes_done = sorted({e["node"] for e in h.events if e.get("type") == "node_end"}
                        | set((report or {}).get("timings_s") or {}))
    return {
        "run_id": h.run_id, "status": h.status, "stage": h.stage, "error": h.error,
        "command": h.opts.command, "city": h.opts.city, "area": h.opts.area,
        "provider": h.opts.provider, "model": h.opts.model,
        "pending_approval": h.pending, "usage": usage, "nodes_done": nodes_done,
        "researched": ((report or {}).get("research") or {}).get("researched"),
        "rounds": rounds or (report or {}).get("rounds") or [],
        "counts": (report or {}).get("counts"),
        "exports": {"xlsx": (run_dir / "leads.xlsx").exists(),
                    "report": report is not None},
    }


# --- app -----------------------------------------------------------------------------------
def create_app(manager: RunManager | None = None) -> FastAPI:
    mgr = manager or RunManager()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        load_env_file()  # server-side defaults for keys a request leaves out
        yield
        await mgr.shutdown()

    app = FastAPI(title="LeadGraph API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"],
                       allow_methods=["*"], allow_headers=["*"])
    app.state.manager = mgr

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/models")
    async def models() -> dict[str, Any]:
        m = load_models()
        return {"providers": {name: {"default": p.default_model,
                                     "models": {mid: price.model_dump()
                                                for mid, price in p.models.items()}}
                              for name, p in m.providers.items()},
                "eval_models": m.eval_models}

    @app.get("/config")
    async def cfg() -> dict[str, Any]:
        """Configured cities and cities looked up by name earlier. Any other city name works
        too: the run looks it up on Google Places (one request) before it starts."""
        known = config.list_cities(config.CONFIG_DIR, mgr.outputs_dir)
        segments = [p.stem for p in sorted((config.CONFIG_DIR / "segments").glob("*.yaml"))]
        return {"cities": {k: list(c.test_areas) for k, c in known.items()},
                "city_labels": {k: c.label for k, c in known.items()},
                "segments": segments}

    @app.post("/runs", status_code=202)
    async def create_run(req: RunRequest) -> dict[str, Any]:
        run_id = make_run_id(req.city, req.area, req.command)
        opts = RunOptions(
            city=req.city, command=req.command, segment=req.segment, area=req.area,
            limit=req.limit, provider=req.provider, model=req.model,
            llm_key=req.keys.llm, places_key=req.keys.places, search_key=req.keys.search,
            max_places_requests=req.budget.max_places_requests,
            max_llm_cost=req.budget.max_llm_cost,
            max_search_requests=req.budget.max_search_requests,
            places_tier=req.places_tier, max_rounds=req.max_rounds,
            use_scout=req.scout and req.command == "discover",
            extra_sources=req.sources, source_run=req.source_run, run_id=run_id,
            outputs_dir=mgr.outputs_dir, approve="none" if req.command != "discover" else None,
        )
        h = RunHandle(run_id=run_id, opts=opts)
        mgr.start(h)
        return {"run_id": run_id, "status": h.status}

    @app.get("/runs")
    async def list_runs() -> list[dict[str, Any]]:
        ids = {p.parent.name for p in mgr.outputs_dir.glob("*/run.json")} | set(mgr.runs)
        return [snapshot(mgr.get(i), mgr.outputs_dir) for i in sorted(ids, reverse=True)]

    @app.get("/runs/{run_id}")
    async def get_run(run_id: str) -> dict[str, Any]:
        return snapshot(mgr.get(run_id), mgr.outputs_dir)

    @app.get("/runs/{run_id}/events")
    async def events(run_id: str) -> StreamingResponse:
        h = mgr.get(run_id)

        async def stream():
            q: asyncio.Queue = asyncio.Queue()
            h.subscribers.add(q)
            try:
                for e in list(h.events):  # replay what happened so far
                    yield f"event: {e['type']}\ndata: {json.dumps(e)}\n\n"
                if h.status in FINAL or h.status in ("awaiting_approval", "interrupted"):
                    yield f"event: end\ndata: {json.dumps({'status': h.status})}\n\n"
                    return
                while True:
                    try:
                        e = await asyncio.wait_for(q.get(), timeout=15)
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    yield f"event: {e['type']}\ndata: {json.dumps(e)}\n\n"
                    if e["type"] == "end" or (e["type"] == "status"
                                              and e["status"] == "awaiting_approval"):
                        return
            finally:
                h.subscribers.discard(q)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    @app.post("/runs/{run_id}/approve-sources", status_code=202)
    async def approve(run_id: str, body: Approval) -> dict[str, Any]:
        h = mgr.get(run_id)
        if h.status != "awaiting_approval":
            raise HTTPException(409, f"run is {h.status}, not awaiting approval")
        if body.keys:  # after a server restart the run's keys are gone
            h.opts.llm_key = body.keys.llm or h.opts.llm_key
            h.opts.places_key = body.keys.places or h.opts.places_key
            h.opts.search_key = body.keys.search or h.opts.search_key
        mgr.start(h, resume=True, decision={"approved": body.approved, "extra": body.extra})
        return {"run_id": run_id, "status": "running"}

    @app.post("/runs/{run_id}/resume", status_code=202)
    async def resume(run_id: str, body: ResumeRequest) -> dict[str, Any]:
        h = mgr.get(run_id)
        if h.status in ("running", "queued"):
            raise HTTPException(409, "run is already running")
        if h.status == "finished":
            raise HTTPException(409, "run already finished")
        h.opts.llm_key = body.keys.llm or h.opts.llm_key
        h.opts.places_key = body.keys.places or h.opts.places_key
        h.opts.search_key = body.keys.search or h.opts.search_key
        mgr.start(h, resume=True)
        return {"run_id": run_id, "status": "running"}

    @app.get("/runs/{run_id}/export.xlsx")
    async def export_xlsx(run_id: str) -> FileResponse:
        path = mgr.outputs_dir / run_id / "leads.xlsx"
        if not path.exists():
            raise HTTPException(404, "no export yet")
        return FileResponse(path, filename=f"{run_id}.xlsx", media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))

    @app.get("/runs/{run_id}/report")
    async def report(run_id: str) -> dict[str, Any]:
        path = mgr.outputs_dir / run_id / "report.json"
        if not path.exists():
            raise HTTPException(404, "no report yet")
        return json.loads(path.read_text(encoding="utf-8"))

    @app.get("/runs/{run_id}/leads")
    async def leads(run_id: str) -> list[dict[str, Any]]:
        path = mgr.outputs_dir / run_id / "companies.jsonl"
        if not path.exists():
            raise HTTPException(404, "no companies yet")
        decisions = _decisions(mgr.outputs_dir / run_id)
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]
        return [{**r, "review_decision": decisions.get(r["id"])} for r in rows]

    @app.post("/runs/{run_id}/review/{company_id}")
    async def review(run_id: str, company_id: str, body: ReviewDecision) -> dict[str, Any]:
        run_dir = mgr.outputs_dir / run_id
        if not (run_dir / "companies.jsonl").exists():
            raise HTTPException(404, "no companies yet")
        decisions = _decisions(run_dir)
        if body.decision == "clear":
            decisions.pop(company_id, None)
        else:
            decisions[company_id] = {"decision": body.decision, "note": body.note,
                                     "at": datetime.now(UTC).isoformat(timespec="seconds")}
        (run_dir / "review_decisions.json").write_text(json.dumps(decisions, indent=2),
                                                       encoding="utf-8")
        return {"company_id": company_id, "review_decision": decisions.get(company_id)}

    return app


def _decisions(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "review_decisions.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


app = create_app()
