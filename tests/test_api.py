"""M8: API flow on fakes — create → SSE → approval → export, restart + resume, no key leaks."""

import asyncio
import json
import logging

import httpx
import pytest
import respx

from api.main import RunManager, create_app
from leadgraph.llm import LLM
from tests.fakes import ScriptedChat
from tests.test_graph_m2 import DIR1, DIR2, QueryPlaces, mount_world, responder, scout_script

SECRET = {"llm": "sk-ant-SECRET-llm-123", "places": "AIza-SECRET-places-456",
          "search": "SECRET-serper-789"}


def make_manager(tmp_path, models, chat):
    def patch(ctx):
        ctx.settings.fetch.per_domain_interval_s = 0
        ctx.deps.llm = LLM(chat, provider="anthropic", model="claude-haiku-5-5",
                           cache=ctx.deps.cache, usage=ctx.deps.usage, models_cfg=models)
    return RunManager(outputs_dir=tmp_path, patch_ctx=patch)


def client_for(manager):
    app = create_app(manager)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


async def wait_status(client, run_id, wanted, timeout=20):
    for _ in range(int(timeout / 0.05)):
        r = (await client.get(f"/runs/{run_id}")).json()
        if r["status"] in wanted:
            return r
        await asyncio.sleep(0.05)
    raise AssertionError(f"status stayed {r['status']}")


@respx.mock
async def test_api_discover_flow_restart_and_no_key_leak(tmp_path, models, caplog, capsys):
    respx.route(host="api").pass_through()
    places = QueryPlaces()
    places.fail_once = set()
    mount_world(places)
    chat = ScriptedChat(responder, scout_script)
    caplog.set_level(logging.DEBUG)
    mgr = make_manager(tmp_path, models, chat)
    body = {"command": "discover", "city": "berlin", "area": "mitte", "max_rounds": 5,
            "keys": SECRET, "budget": {"max_places_requests": 200, "max_llm_cost": 1}}

    async with client_for(mgr) as client:
        assert (await client.get("/health")).json() == {"status": "ok"}
        models_resp = (await client.get("/models")).json()
        assert "claude-haiku-5-5" in models_resp["providers"]["anthropic"]["models"]
        assert (await client.get("/config")).json()["cities"]["berlin"] == ["mitte"]

        r = await client.post("/runs", json=body)
        assert r.status_code == 202
        run_id = r.json()["run_id"]
        snap = await wait_status(client, run_id, {"awaiting_approval"})
        assert [c["url"] for c in snap["pending_approval"]["candidates"]] == [DIR1, DIR2]

        # SSE replays the history and ends at the approval pause
        async with client.stream("GET", f"/runs/{run_id}/events") as resp:
            text = "".join([chunk async for chunk in resp.aiter_text()])
        assert "event: node_start" in text and '"node": "scout_sources"' in text
        assert '"status": "awaiting_approval"' in text

        # --- server restart: a new manager has no keys in memory ---------------------------
        await mgr.shutdown()
    mgr2 = make_manager(tmp_path, models, chat)
    async with client_for(mgr2) as client:
        snap = (await client.get(f"/runs/{run_id}")).json()
        assert snap["status"] == "awaiting_approval" and snap["pending_approval"]
        r = await client.post(f"/runs/{run_id}/approve-sources", json={"approved": [5]})
        assert r.status_code == 202  # out-of-range index is ignored by the graph
        snap = await wait_status(client, run_id, {"awaiting_approval", "failed", "finished"})
        assert snap["status"] == "awaiting_approval" and "key missing" in snap["error"]

        r = await client.post(f"/runs/{run_id}/approve-sources",
                              json={"approved": [0], "keys": SECRET})
        assert r.status_code == 202
        events_task = asyncio.create_task(_collect(client, f"/runs/{run_id}/events"))
        snap = await wait_status(client, run_id, {"finished", "failed"}, timeout=30)
        assert snap["status"] == "finished", snap["error"]
        events = await events_task
        assert any(e.get("round_result") for e in events)  # per-round yield streamed
        assert snap["rounds"][-1]["cumulative"] == 15
        rep = (await client.get(f"/runs/{run_id}/report")).json()
        assert [s["url"] for s in rep["sources"]["approved"]] == [DIR1]

        x = await client.get(f"/runs/{run_id}/export.xlsx")
        assert x.status_code == 200 and x.content[:2] == b"PK"
        leads = (await client.get(f"/runs/{run_id}/leads")).json()
        cid = next(c["id"] for c in leads if c.get("is_property_manager"))
        r = await client.post(f"/runs/{run_id}/review/{cid}", json={"decision": "reject"})
        assert r.json()["review_decision"]["decision"] == "reject"
        leads = (await client.get(f"/runs/{run_id}/leads")).json()
        assert next(c for c in leads if c["id"] == cid)["review_decision"]["decision"] == "reject"
        listing = (await client.get("/runs")).json()
        assert listing[0]["run_id"] == run_id
        assert mgr2.runs[run_id].opts.llm_key is None  # keys dropped when the run ended

    # --- no key anywhere: logs, stdout/stderr, responses, files ----------------------------
    out = capsys.readouterr()
    haystack = caplog.text + out.out + out.err + json.dumps(snap) + json.dumps(listing)
    for f in tmp_path.rglob("*"):
        if f.is_file():
            haystack += f.read_bytes().decode("utf-8", "ignore")
    for secret in SECRET.values():
        assert secret not in haystack


async def _collect(client, url):
    events = []
    async with client.stream("GET", url) as resp:
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


@respx.mock
async def test_api_resume_after_failure(tmp_path, models):
    respx.route(host="api").pass_through()
    places = QueryPlaces()  # fails once on a round-2 query
    mount_world(places)
    chat = ScriptedChat(responder, scout_script)
    mgr = make_manager(tmp_path, models, chat)
    body = {"command": "discover", "city": "berlin", "area": "mitte", "max_rounds": 5,
            "scout": False, "sources": [DIR1], "keys": SECRET}
    async with client_for(mgr) as client:
        run_id = (await client.post("/runs", json=body)).json()["run_id"]
        snap = await wait_status(client, run_id, {"failed", "finished"}, timeout=30)
        assert snap["status"] == "failed" and "simulated failure" in snap["error"]
        assert (await client.post(f"/runs/{run_id}/approve-sources", json={})).status_code == 409
        r = await client.post(f"/runs/{run_id}/resume", json={"keys": SECRET})
        assert r.status_code == 202
        snap = await wait_status(client, run_id, {"failed", "finished"}, timeout=30)
        assert snap["status"] == "finished", snap["error"]
        assert (await client.post(f"/runs/{run_id}/resume", json={})).status_code == 409


@pytest.mark.parametrize("path", ["/runs/nope", "/runs/nope/export.xlsx", "/runs/nope/leads"])
async def test_api_unknown_run(tmp_path, path):
    async with client_for(RunManager(outputs_dir=tmp_path)) as client:
        assert (await client.get(path)).status_code == 404
