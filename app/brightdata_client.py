"""
Bright Data Scraper Studio AI Flow API Client.

A typed async client for Bright Data's Scraper Studio AI Flow APIs,
supporting the full self-healing loop:
  create → trigger AI → poll → run → health-check → heal → auto-approve → re-scrape → verify

When BRIGH...KEY is not set, operates in MOCK mode with realistic simulated responses
for local development and demos.
"""

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx

API_BASE = "https://api.brightdata.com/dca"
API_KEY = os.environ.get("BRIGHTDATA_API_KEY", "")


class JobStatus(str, Enum):
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    PENDING_ANSWER = "pending_answer"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Collector:
    collector_id: str
    name: str
    url: str
    description: str
    schema_fields: list[str]
    created_at: float = field(default_factory=time.time)


@dataclass
class HealEvent:
    event_id: str
    collector_id: str
    trigger_reason: str
    heal_prompt: str
    proposed_diff: str | None = None
    auto_approved: bool = False
    status: str = "initiated"
    timestamp: float = field(default_factory=time.time)


class BrightDataClient:
    """Async client for Bright Data Scraper Studio AI Flow API."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or API_KEY
        self.mock_mode = not bool(self.api_key)
        if self.mock_mode:
            self._mock_collectors: dict[str, dict] = {}
            self._mock_jobs: dict[str, dict] = {}
            self._mock_datasets: dict[str, list[dict]] = {}
            self._mock_heal_jobs: dict[str, dict] = {}

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    # ── Workflow 1: Create scraper from scratch ──────────────────────

    async def create_collector(
        self, name: str, url: str, webhook_endpoint: str | None = None
    ) -> str:
        """POST /dca/collector → returns collector_id."""
        if self.mock_mode:
            cid = f"c_mock_{uuid.uuid4().hex[:12]}"
            self._mock_collectors[cid] = {
                "name": name,
                "url": url,
                "status": "created",
            }
            return cid

        payload = {
            "name": name,
            "deliver": {
                "type": "webhook" if webhook_endpoint else "api",
                "endpoint": webhook_endpoint or "",
                "delivery_type": "deliver_results",
            },
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{API_BASE}/collector", json=payload, headers=self._headers()
            )
            resp.raise_for_status()
            data = resp.json()
            return data["collector_id"]

    async def trigger_ai_flow(
        self, collector_id: str, description: str, urls: list[str]
    ) -> str:
        """POST /dca/collectors/{id}/automate_template → returns AI job id."""
        if self.mock_mode:
            job_id = f"ai_mock_{uuid.uuid4().hex[:12]}"
            self._mock_jobs[job_id] = {
                "collector_id": collector_id,
                "status": JobStatus.QUEUED.value,
                "description": description,
                "urls": urls,
                "created_at": time.time(),
            }
            # Simulate completion after 2 seconds
            asyncio.get_event_loop().call_later(
                2,
                lambda: self._mock_jobs.__setitem__(
                    job_id,
                    {**self._mock_jobs[job_id], "status": JobStatus.COMPLETED.value},
                ),
            )
            return job_id

        payload = {"description": description, "urls": urls}
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{API_BASE}/collectors/{collector_id}/automate_template",
                json=payload,
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json().get("id", "")

    async def poll_ai_flow_progress(self, collector_id: str, job_id: str) -> dict:
        """GET /dca/collectors/{id}/automate_template/progress."""
        if self.mock_mode:
            job = self._mock_jobs.get(job_id, {})
            return {"status": job.get("status", "unknown"), "progress": 100}

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{API_BASE}/collectors/{collector_id}/automate_template/progress",
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    # ── Trigger + fetch results ──────────────────────────────────────

    async def trigger_collector(self, collector_id: str) -> str:
        """POST /dca/trigger → returns job_id."""
        if self.mock_mode:
            job_id = f"run_mock_{uuid.uuid4().hex[:12]}"
            # Generate mock scrape results
            self._mock_datasets[job_id] = self._generate_mock_results(collector_id)
            return job_id

        payload = {"collector": collector_id}
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{API_BASE}/trigger", json=payload, headers=self._headers()
            )
            resp.raise_for_status()
            return resp.json().get("job_id", "")

    async def fetch_dataset(self, job_id: str) -> list[dict]:
        """GET /dca/dataset?id={job_id} → returns rows."""
        if self.mock_mode:
            return self._mock_datasets.get(job_id, [])

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{API_BASE}/dataset", params={"id": job_id}, headers=self._headers()
            )
            if resp.status_code == 202:
                await asyncio.sleep(3)
                return await self.fetch_dataset(job_id)
            resp.raise_for_status()
            return resp.json()

    # ── Workflow 2: Self-healing ────────────────────────────────────

    async def trigger_self_heal(
        self, collector_id: str, prompt: str, custom_input: list | None = None
    ) -> str:
        """POST /dca/collectors/{id}/refactor_template → returns heal job id."""
        if self.mock_mode:
            heal_id = f"heal_mock_{uuid.uuid4().hex[:12]}"
            self._mock_heal_jobs[heal_id] = {
                "collector_id": collector_id,
                "prompt": prompt,
                "status": JobStatus.IN_PROGRESS.value,
                "proposed_diff": self._generate_mock_diff(prompt),
                "created_at": time.time(),
            }
            # Simulate pending_answer after 2 seconds
            asyncio.get_event_loop().call_later(
                2,
                lambda: self._mock_heal_jobs.__setitem__(
                    heal_id,
                    {
                        **self._mock_heal_jobs[heal_id],
                        "status": JobStatus.PENDING_ANSWER.value,
                    },
                ),
            )
            return heal_id

        payload: dict[str, Any] = {"prompt": prompt}
        if custom_input:
            payload["custom_input"] = custom_input

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{API_BASE}/collectors/{collector_id}/refactor_template",
                json=payload,
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json().get("id", "")

    async def poll_heal_progress(self, collector_id: str, heal_id: str) -> dict:
        """GET /dca/collectors/{id}/refactor_template/progress."""
        if self.mock_mode:
            job = self._mock_heal_jobs.get(heal_id, {})
            return {
                "status": job.get("status", "unknown"),
                "proposed_diff": job.get("proposed_diff"),
            }

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{API_BASE}/collectors/{collector_id}/refactor_template/progress",
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    async def resume_automation_job(
        self, collector_id: str, heal_id: str, approve: bool = True, auto_save: bool = True
    ) -> bool:
        """POST /dca/collectors/{id}/resume_automation_job — THE UNLOCK.

        Auto-approve the proposed self-healing diff without human intervention.
        The official Bright Data demo repo couldn't do this — they exit with code 3
        ("awaiting approval") when the heal hits pending_answer.

        This endpoint accepts (message: true, auto_save: true) to silently accept
        the AI's proposed extraction diff and persist it to the collector.
        """
        if self.mock_mode:
            self._mock_heal_jobs[heal_id]["status"] = JobStatus.COMPLETED.value
            return True

        payload = {"message": approve, "auto_save": auto_save}
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{API_BASE}/collectors/{collector_id}/resume_automation_job",
                json=payload,
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.status_code == 200

    # ── Mock helpers ─────────────────────────────────────────────────

    def _generate_mock_results(self, collector_id: str) -> list[dict]:
        """Generate realistic mock scrape results for demo."""
        c = self._mock_collectors.get(collector_id, {})
        url = c.get("url", "https://example.com")

        # Different result sets based on URL patterns
        if "news.ycombinator.com" in url or "hn" in url.lower():
            return [
                {"title": "Show HN: Scraper-Health-MCP – Autonomous self-healing scraper fleet",
                 "url": "https://news.ycombinator.com/item?id=42",
                 "points": 342, "author": "sentinel_dev"},
                {"title": "The end of brittle scrapers: AI agents that fix themselves",
                 "url": "https://news.ycombinator.com/item?id=43",
                 "points": 189, "author": "brightdata_fan"},
                {"title": "Bright Data's resume_automation_job: the hidden self-healing unlock",
                 "url": "https://news.ycombinator.com/item?id=44",
                 "points": 97, "author": "0xConsole"},
                {"title": "MCP servers for scraping infrastructure: a new pattern",
                 "url": "https://news.ycombinator.com/item?id=45",
                 "points": 56, "author": "agent_builder"},
                {"title": "Self-healing pipelines: from anomaly detection to auto-repair",
                 "url": "https://news.ycombinator.com/item?id=46",
                 "points": 23, "author": "devops_eng"},
            ]
        elif "shop" in url.lower() or "store" in url.lower() or "product" in url.lower():
            return [
                {"name": "Wireless Mechanical Keyboard", "price": "$129.99",
                 "rating": "4.5", "availability": "In Stock"},
                {"name": "USB-C Hub 7-in-1", "price": "$49.99",
                 "rating": "4.2", "availability": "In Stock"},
                {"name": "4K Monitor 27\"", "price": "$399.00",
                 "rating": "4.7", "availability": "2 Left"},
                {"name": "Laptop Stand Aluminum", "price": "$34.99",
                 "rating": "4.3", "availability": "In Stock"},
                {"name": "Webcam 1080p", "price": "$59.99",
                 "rating": "4.0", "availability": "Out of Stock"},
            ]
        else:
            return [
                {"title": "Sample Article 1", "date": "2026-08-17",
                 "author": "Demo Author", "content": "Lorem ipsum..."},
                {"title": "Sample Article 2", "date": "2026-08-16",
                 "author": "Another Author", "content": "Dolor sit amet..."},
                {"title": "Sample Article 3", "date": "2026-08-15",
                 "author": "Third Author", "content": "Consectetur adipiscing..."},
            ]

    def _generate_mock_diff(self, prompt: str) -> str:
        """Generate a realistic self-healing diff for the demo."""
        return f"""--- a/scraper.js
+++ b/scraper.js
@@ -15,7 +15,7 @@
-  const title = document.querySelector('.story-title').textContent;
+  const title = document.querySelector('h2.title-link').textContent;
@@ -28,7 +28,7 @@
-  const price = document.querySelector('.price-tag').textContent;
+  const price = document.querySelector('[data-testid="price-value"]').textContent;
 
Heal prompt: {prompt}
Status: AI proposed new selectors to adapt to target site structure change."""
