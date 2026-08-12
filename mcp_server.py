"""
MCP Server — exposes the self-healing scraper agent as 5 MCP tools.

Tools:
  1. create_scraper  — Create a new scraper from a URL + description
  2. run_collector   — Trigger a collector and return results
  3. health_check    — Run a collector and check its health
  4. self_heal       — Execute the full autonomous self-healing loop
  5. verify          — Re-run a healed collector and verify health

This MCP server can be called from Claude Code, Cursor, or any MCP-compatible
AI agent to manage a self-healing scraper fleet programmatically.
"""

import json
from typing import Any

from heal_orchestrator import orch, HealOrchestrator
from brightdata_client import BrightDataClient


# ── MCP Tool definitions ──────────────────────────────────────────────

MCP_TOOLS = [
    {
        "name": "create_scraper",
        "description": "Create a new scraper/collector from a URL and natural-language description. "
                       "Uses Bright Data's AI Flow to auto-generate the extraction schema and code. "
                       "Returns the collector_id for subsequent operations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Human-readable name for the scraper"},
                "url": {"type": "string", "description": "Target URL to scrape"},
                "description": {"type": "string", "description": "Natural-language description of what data to extract"},
                "schema_fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of required field names for health checking"
                },
            },
            "required": ["name", "url", "description", "schema_fields"],
        },
    },
    {
        "name": "run_collector",
        "description": "Trigger a collector to run a scrape and return the results. "
                       "This is a synchronous operation that waits for the scrape to complete.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "collector_id": {"type": "string", "description": "The collector ID to run"},
            },
            "required": ["collector_id"],
        },
    },
    {
        "name": "health_check",
        "description": "Run a collector and perform a full health check on its output. "
                       "Returns the health status (healthy/degraded/broken) with detailed diagnostics "
                       "including null fields, row-count anomalies, and schema drift.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "collector_id": {"type": "string", "description": "The collector ID to check"},
            },
            "required": ["collector_id"],
        },
    },
    {
        "name": "self_heal",
        "description": "Execute the full autonomous self-healing loop for a collector. "
                       "Detects breakage → triggers AI self-heal → auto-approves the proposed diff "
                       "via resume_automation_job → re-scrapes → verifies. "
                       "Escalates to full regeneration if healing fails after retries. "
                       "THIS IS THE KEY INNOVATION: the auto-approval step that the official "
                       "Bright Data demo couldn't perform.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "collector_id": {"type": "string", "description": "The collector ID to heal"},
                "heal_prompt": {
                    "type": "string",
                    "description": "Optional: custom heal prompt. If not provided, auto-generated from health report.",
                },
                "max_retries": {
                    "type": "integer",
                    "description": "Max heal attempts before escalating to regeneration (default: 2)",
                    "default": 2,
                },
            },
            "required": ["collector_id"],
        },
    },
    {
        "name": "verify",
        "description": "Re-run a previously healed collector and verify that its health has been restored. "
                       "Returns the health report comparing before/after states.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "collector_id": {"type": "string", "description": "The collector ID to verify"},
            },
            "required": ["collector_id"],
        },
    },
]


async def handle_tool_call(tool_name: str, arguments: dict[str, Any]) -> dict:
    """Handle an MCP tool call and return the result."""

    if tool_name == "create_scraper":
        # Create collector via Bright Data API
        collector_id = await orch.client.create_collector(
            name=arguments["name"],
            url=arguments["url"],
        )
        # Trigger AI Flow to generate schema
        ai_job_id = await orch.client.trigger_ai_flow(
            collector_id,
            arguments["description"],
            [arguments["url"]],
        )
        # Poll until AI Flow completes (simplified — in prod, poll async)
        import asyncio
        for _ in range(30):
            await asyncio.sleep(2)
            progress = await orch.client.poll_ai_flow_progress(collector_id, ai_job_id)
            if progress.get("status") in ("completed", "COMPLETED"):
                break

        # Register in orchestrator
        collector = orch.register_collector(
            collector_id=collector_id,
            name=arguments["name"],
            url=arguments["url"],
            description=arguments["description"],
            schema_fields=arguments["schema_fields"],
        )
        return {
            "collector_id": collector_id,
            "name": collector.name,
            "status": "created",
            "ai_flow_status": progress.get("status"),
        }

    elif tool_name == "run_collector":
        collector_id = arguments["collector_id"]
        job_id = await orch.client.trigger_collector(collector_id)
        import asyncio
        await asyncio.sleep(1)
        results = await orch.client.fetch_dataset(job_id)
        return {
            "collector_id": collector_id,
            "job_id": job_id,
            "row_count": len(results),
            "results": results[:10],  # first 10 rows
        }

    elif tool_name == "health_check":
        collector_id = arguments["collector_id"]
        results, report = await orch.run_and_check(collector_id)
        return {
            "collector_id": collector_id,
            "health": report.to_dict(),
            "sample_results": results[:3],
        }

    elif tool_name == "self_heal":
        collector_id = arguments["collector_id"]
        heal_prompt = arguments.get("heal_prompt")
        max_retries = arguments.get("max_retries", 2)
        result = await orch.self_heal(collector_id, heal_prompt, max_retries)
        return result.to_dict()

    elif tool_name == "verify":
        collector_id = arguments["collector_id"]
        results, report = await orch.run_and_check(collector_id)
        return {
            "collector_id": collector_id,
            "health": report.to_dict(),
            "verified": report.status.value == "healthy",
            "row_count": len(results),
        }

    else:
        return {"error": f"Unknown tool: {tool_name}"}


def get_mcp_manifest() -> dict:
    """Return the MCP server manifest."""
    return {
        "name": "scraper-health-mcp",
        "version": "1.0.0",
        "description": "Autonomous self-healing scraper fleet manager for Bright Data Scraper Studio. "
                       "Detects breakage, triggers AI self-healing, auto-approves diffs, and verifies recovery.",
        "tools": MCP_TOOLS,
    }
