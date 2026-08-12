"""
Scraper-Health-MCP — FastAPI Application.

Autonomous self-healing scraper fleet manager for Bright Data Scraper Studio.
"""

import asyncio
import json
import os
import time

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from heal_orchestrator import orch, HealOutcome
from health_checker import HealthStatus
from mcp_server import MCP_TOOLS, handle_tool_call, get_mcp_manifest

app = FastAPI(
    title="Scraper-Health-MCP",
    description="Autonomous self-healing scraper fleet manager for Bright Data Scraper Studio",
    version="1.0.0",
)

# Paths (relative to this file)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")

templates = Jinja2Templates(directory=TEMPLATES_DIR)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ── Models ────────────────────────────────────────────────────────────

class CreateScraperRequest(BaseModel):
    name: str
    url: str
    description: str
    schema_fields: list[str]


class RunCollectorRequest(BaseModel):
    collector_id: str


class HealthCheckRequest(BaseModel):
    collector_id: str


class SelfHealRequest(BaseModel):
    collector_id: str
    heal_prompt: str | None = None
    max_retries: int = 2


class VerifyRequest(BaseModel):
    collector_id: str


class TriggerBreakageRequest(BaseModel):
    collector_id: str


# ── Dashboard routes ──────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main dashboard — collector list + heal-event timeline."""
    status = orch.get_status()
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "status": status,
    })


# ── API endpoints ─────────────────────────────────────────────────────

@app.get("/api/health")
async def api_health():
    """Health check endpoint."""
    status = orch.get_status()
    return {
        "status": "ok",
        "timestamp": time.time(),
        "collectors_count": len(status["collectors"]),
        "heal_events_count": len(status["heal_events"]),
        "mock_mode": status["mock_mode"],
    }


@app.get("/api/status")
async def api_status():
    """Full orchestrator status."""
    return orch.get_status()


@app.get("/api/mcp/manifest")
async def mcp_manifest():
    """MCP server manifest — tool definitions for AI agents."""
    return get_mcp_manifest()


@app.post("/api/mcp/tools/{tool_name}")
async def mcp_call_tool(tool_name: str, request: Request):
    """Call an MCP tool by name with JSON arguments."""
    body = await request.json()
    result = await handle_tool_call(tool_name, body)
    return result


# ── MCP tools as REST endpoints ───────────────────────────────────────

@app.post("/api/create_scraper")
async def api_create_scraper(req: CreateScraperRequest):
    """MCP Tool 1: Create a new scraper from URL + description."""
    result = await handle_tool_call("create_scraper", req.model_dump())
    return result


@app.post("/api/run_collector")
async def api_run_collector(req: RunCollectorRequest):
    """MCP Tool 2: Run a collector and return results."""
    result = await handle_tool_call("run_collector", {"collector_id": req.collector_id})
    return result


@app.post("/api/health_check")
async def api_health_check(req: HealthCheckRequest):
    """MCP Tool 3: Run collector + health check."""
    result = await handle_tool_call("health_check", {"collector_id": req.collector_id})
    return result


@app.post("/api/self_heal")
async def api_self_heal(req: SelfHealRequest):
    """MCP Tool 4: Full autonomous self-healing loop."""
    result = await handle_tool_call("self_heal", req.model_dump())
    return result


@app.post("/api/verify")
async def api_verify(req: VerifyRequest):
    """MCP Tool 5: Verify healed collector health."""
    result = await handle_tool_call("verify", {"collector_id": req.collector_id})
    return result


@app.get("/api/tools")
async def api_tools():
    """List all MCP tools."""
    return {"tools": MCP_TOOLS, "count": len(MCP_TOOLS)}


# ── Demo endpoints ────────────────────────────────────────────────────

@app.post("/api/demo")
async def api_demo():
    """Run a full demo: health check all collectors + simulate a heal."""
    demo_results = {
        "timestamp": time.time(),
        "collectors": [],
        "heal_demonstrated": False,
        "steps": [],
    }

    # Step 1: Check all collectors
    for cid in list(orch.collectors.keys()):
        results, report = await orch.run_and_check(cid)
        demo_results["collectors"].append({
            "collector_id": cid,
            "name": orch.collectors[cid].name,
            "health": report.to_dict(),
            "row_count": len(results),
        })
    demo_results["steps"].append("Health checked all 3 collectors")

    # Step 2: Simulate breakage + heal on first collector
    first_cid = list(orch.collectors.keys())[0]
    collector = orch.collectors[first_cid]

    # Inject a broken result to simulate breakage
    checker = orch.health_checkers[first_cid]
    original_baseline = checker._row_history.copy()

    # Simulate breakage: set baseline high, then get 0 results
    checker._row_history = [5, 5, 5, 5, 5]

    # Trigger heal
    heal_result = await orch.self_heal(first_cid, heal_prompt="Fix the title selector — it moved to h2.title-link")
    demo_results["heal_demonstrated"] = heal_result.outcome == HealOutcome.HEALED
    demo_results["heal_result"] = heal_result.to_dict()
    demo_results["steps"].append(f"Simulated breakage on {collector.name}")
    demo_results["steps"].append("Triggered self-heal -> AI proposed diff")
    demo_results["steps"].append("Auto-approved via resume_automation_job -> healed!")
    demo_results["steps"].append("Re-scraped -> health restored")

    # Restore baseline
    checker._row_history = original_baseline

    return demo_results


@app.post("/api/trigger_breakage")
async def api_trigger_breakage(req: TriggerBreakageRequest):
    """Simulate a site change that breaks a scraper, then trigger heal."""
    cid = req.collector_id
    collector = orch.collectors.get(cid)
    if not collector:
        return {"error": "Collector not found"}

    # Corrupt the health checker baseline to simulate breakage
    checker = orch.health_checkers[cid]
    checker._row_history = [5, 5, 5, 5, 5]  # Set expected to 5

    # Now trigger heal
    heal_result = await orch.self_heal(
        cid,
        heal_prompt=f"The page structure changed. {collector.description} — update selectors.",
    )

    return {
        "collector_id": cid,
        "collector_name": collector.name,
        "heal_result": heal_result.to_dict(),
    }


@app.post("/api/reset")
async def api_reset():
    """Reset all collectors and heal events (demo convenience)."""
    global orch
    from heal_orchestrator import HealOrchestrator
    orch = HealOrchestrator()
    if orch.client.mock_mode:
        from heal_orchestrator import seed_demo_collectors
        seed_demo_collectors()
    return {"status": "reset", "collectors": len(orch.collectors)}


# ── Run ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
