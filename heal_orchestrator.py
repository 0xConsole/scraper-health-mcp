"""
Heal Orchestrator — the autonomous self-healing loop.

This is the core of our project: it ties together the Bright Data client
and health checker into a fully autonomous self-healing pipeline:

  detect breakage → trigger heal → poll → auto-approve → re-scrape → verify

If healing fails, it escalates to system-level regeneration (create a new
scraper from scratch via Workflow 1).

The `resume_automation_job` auto-approval step is our key differentiator —
the official Bright Data demo repo couldn't do this and exits with code 3
("awaiting approval") when the AI proposes a diff.
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from brightdata_client import (
    BrightDataClient,
    JobStatus,
    Collector,
    HealEvent,
)
from health_checker import HealthChecker, HealthStatus, HealthReport


class HealOutcome(str, Enum):
    NOT_NEEDED = "not_needed"
    HEALED = "healed"
    REGENERATED = "regenerated"
    FAILED = "failed"


@dataclass
class HealResult:
    outcome: HealOutcome
    heal_event: HealEvent | None = None
    report_before: HealthReport | None = None
    report_after: HealthReport | None = None
    duration_seconds: float = 0.0
    escalation_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "outcome": self.outcome.value,
            "heal_event": (
                {
                    "event_id": self.heal_event.event_id,
                    "trigger_reason": self.heal_event.trigger_reason,
                    "heal_prompt": self.heal_event.heal_prompt,
                    "auto_approved": self.heal_event.auto_approved,
                    "status": self.heal_event.status,
                    "timestamp": self.heal_event.timestamp,
                }
                if self.heal_event
                else None
            ),
            "health_before": self.report_before.to_dict() if self.report_before else None,
            "health_after": self.report_after.to_dict() if self.report_after else None,
            "duration_seconds": round(self.duration_seconds, 2),
            "escalation_reason": self.escalation_reason,
        }


class HealOrchestrator:
    """
    Orchestrates the full self-healing loop autonomously.

    Flow:
        1. Run collector → fetch results
        2. Health-check results
        3. If broken → trigger self-heal (refactor_template)
        4. Poll until pending_answer → AUTO-APPROVE (resume_automation_job)
        5. Re-scrape → verify health
        6. If still broken → escalate: regenerate from scratch
    """

    def __init__(self, client: BrightDataClient | None = None):
        self.client = client or BrightDataClient()
        self.collectors: dict[str, Collector] = {}
        self.health_checkers: dict[str, HealthChecker] = {}
        self.heal_events: list[HealEvent] = []
        self._heal_history: dict[str, list[HealResult]] = {}

    def register_collector(
        self,
        collector_id: str,
        name: str,
        url: str,
        description: str,
        schema_fields: list[str],
    ) -> Collector:
        """Register a collector for health monitoring."""
        collector = Collector(
            collector_id=collector_id,
            name=name,
            url=url,
            description=description,
            schema_fields=schema_fields,
        )
        self.collectors[collector_id] = collector
        self.health_checkers[collector_id] = HealthChecker(
            required_fields=schema_fields
        )
        # Also register in mock client so it knows the URL for generating results
        if self.client.mock_mode:
            self.client._mock_collectors[collector_id] = {
                "name": name,
                "url": url,
                "status": "created",
            }
        return collector

    async def run_and_check(self, collector_id: str) -> tuple[list[dict], HealthReport]:
        """Run a collector and health-check its output."""
        # Trigger scrape
        job_id = await self.client.trigger_collector(collector_id)

        # Small wait for async processing
        await asyncio.sleep(1)

        # Fetch results
        results = await self.client.fetch_dataset(job_id)

        # Health check
        checker = self.health_checkers.get(collector_id)
        if not checker:
            raise ValueError(f"Collector {collector_id} not registered")

        # First run establishes baseline
        if not checker._row_history:
            checker.update_baseline(len(results))

        report = checker.check(results)

        # Update baseline on healthy runs
        if report.status == HealthStatus.HEALTHY:
            checker.update_baseline(len(results))

        return results, report

    async def self_heal(
        self,
        collector_id: str,
        heal_prompt: str | None = None,
        max_retries: int = 2,
    ) -> HealResult:
        """
        Execute the full autonomous self-healing loop.

        1. Run + check health
        2. If broken → trigger heal → poll → auto-approve → re-scrape → verify
        3. If still broken after retries → escalate to regeneration
        """
        start_time = time.time()

        # Step 1: Initial run + health check
        _, report_before = await self.run_and_check(collector_id)

        if report_before.status == HealthStatus.HEALTHY:
            return HealResult(
                outcome=HealOutcome.NOT_NEEDED,
                report_before=report_before,
                duration_seconds=time.time() - start_time,
            )

        # Step 2: Construct heal prompt from health report
        if not heal_prompt:
            heal_prompt = self._build_heal_prompt(report_before)

        # Step 3: Trigger self-healing
        heal_event = HealEvent(
            event_id=str(uuid.uuid4()),
            collector_id=collector_id,
            trigger_reason="; ".join(report_before.issues[:3]),
            heal_prompt=heal_prompt,
            status="initiated",
        )
        self.heal_events.append(heal_event)

        for attempt in range(max_retries):
            heal_id = await self.client.trigger_self_heal(collector_id, heal_prompt)
            heal_event.status = "healing"

            # Step 4: Poll until pending_answer or completed
            max_polls = 30
            for _ in range(max_polls):
                await asyncio.sleep(2)
                progress = await self.client.poll_heal_progress(collector_id, heal_id)
                status = progress.get("status", "")

                if status == JobStatus.PENDING_ANSWER.value:
                    heal_event.proposed_diff = progress.get("proposed_diff", "")

                    # THE UNLOCK: auto-approve the diff
                    approved = await self.client.resume_automation_job(
                        collector_id, heal_id, approve=True, auto_save=True
                    )
                    heal_event.auto_approved = approved
                    heal_event.status = "auto_approved"
                    break

                if status == JobStatus.COMPLETED.value:
                    heal_event.status = "completed"
                    break

                if status == JobStatus.FAILED.value:
                    heal_event.status = "failed"
                    break
            else:
                heal_event.status = "timeout"

            # Step 5: Re-scrape and verify
            await asyncio.sleep(1)
            _, report_after = await self.run_and_check(collector_id)

            if report_after.status == HealthStatus.HEALTHY:
                heal_event.status = "healed"
                result = HealResult(
                    outcome=HealOutcome.HEALED,
                    heal_event=heal_event,
                    report_before=report_before,
                    report_after=report_after,
                    duration_seconds=time.time() - start_time,
                )
                self._record_heal(collector_id, result)
                return result

            # Update prompt for retry
            heal_prompt = self._build_heal_prompt(report_after)

        # Step 6: Escalate — regenerate scraper from scratch
        collector = self.collectors.get(collector_id)
        if collector:
            new_id = await self.client.create_collector(
                name=f"{collector.name} (regenerated)",
                url=collector.url,
            )
            await self.client.trigger_ai_flow(
                new_id,
                collector.description,
                [collector.url],
            )
            heal_event.status = "escalated_regenerated"
            result = HealResult(
                outcome=HealOutcome.REGENERATED,
                heal_event=heal_event,
                report_before=report_before,
                duration_seconds=time.time() - start_time,
                escalation_reason="Self-healing failed after max retries — regenerated scraper from scratch",
            )
            self._record_heal(collector_id, result)
            return result

        result = HealResult(
            outcome=HealOutcome.FAILED,
            heal_event=heal_event,
            report_before=report_before,
            duration_seconds=time.time() - start_time,
            escalation_reason="Regeneration not available — collector not registered",
        )
        self._record_heal(collector_id, result)
        return result

    def _build_heal_prompt(self, report: HealthReport) -> str:
        """Construct a targeted heal prompt from the health report."""
        parts = []
        if report.null_fields:
            parts.append(
                f"Fix these broken fields: {', '.join(report.null_fields)}. "
                "The selectors may have moved or the DOM structure changed."
            )
        if report.z_score < -report.z_score if hasattr(report, 'z_score') else False:
            parts.append(f"Row count dropped to {report.row_count} from expected ~{report.expected_rows:.0f}.")
        if not parts:
            parts.append("The scraper output has degraded. Update the extraction selectors to match the current page structure.")
        return " ".join(parts)

    def _record_heal(self, collector_id: str, result: HealResult) -> None:
        if collector_id not in self._heal_history:
            self._heal_history[collector_id] = []
        self._heal_history[collector_id].append(result)

    def get_status(self) -> dict:
        """Get full orchestrator status for dashboard."""
        return {
            "collectors": [
                {
                    "collector_id": c.collector_id,
                    "name": c.name,
                    "url": c.url,
                    "description": c.description,
                    "schema_fields": c.schema_fields,
                    "created_at": c.created_at,
                    "health": self.health_checkers[c.collector_id].to_dict()
                        if c.collector_id in self.health_checkers else None,
                }
                for c in self.collectors.values()
            ],
            "heal_events": [
                {
                    "event_id": e.event_id,
                    "collector_id": e.collector_id,
                    "trigger_reason": e.trigger_reason,
                    "heal_prompt": e.heal_prompt,
                    "proposed_diff": e.proposed_diff,
                    "auto_approved": e.auto_approved,
                    "status": e.status,
                    "timestamp": e.timestamp,
                }
                for e in self.heal_events
            ],
            "heal_history": {
                cid: [r.to_dict() for r in results]
                for cid, results in self._heal_history.items()
            },
            "mock_mode": self.client.mock_mode,
        }


# ── Global singleton ──────────────────────────────────────────────────
orchestrator = HealOrchestrator()


def seed_demo_collectors() -> None:
    """Seed the orchestrator with demo collectors for the hackathon demo."""
    # Collector 1: Hacker News top stories
    orch.register_collector(
        collector_id="c_demo_hn",
        name="Hacker News Top Stories",
        url="https://news.ycombinator.com",
        description="Extract top stories: title, url, points, author from HN front page",
        schema_fields=["title", "url", "points", "author"],
    )
    # Collector 2: E-commerce product listings
    orch.register_collector(
        collector_id="c_demo_shop",
        name="E-Commerce Product Listings",
        url="https://example-store.com/products",
        description="Extract product name, price, rating, availability from product listing page",
        schema_fields=["name", "price", "rating", "availability"],
    )
    # Collector 3: Documentation site
    orch.register_collector(
        collector_id="c_demo_docs",
        name="Documentation Articles",
        url="https://docs.example.com/guides",
        description="Extract article title, date, author, content from documentation pages",
        schema_fields=["title", "date", "author", "content"],
    )
    # Seed baselines with known-good row counts so initial checks report healthy
    orch.health_checkers["c_demo_hn"]._row_history = [5, 5, 5]
    orch.health_checkers["c_demo_shop"]._row_history = [5, 5, 5]
    orch.health_checkers["c_demo_docs"]._row_history = [3, 3, 3]


# Initialize with the global orchestrator
orch = orchestrator

# Auto-seed demo data on import if in mock mode
if orchestrator.client.mock_mode:
    seed_demo_collectors()
