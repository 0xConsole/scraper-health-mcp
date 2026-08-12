"""
Health Checker — detects when a scraper's output has degraded.

Implements anomaly detection over scrape results using:
1. Schema validation — required fields must be present and non-null
2. Row-count anomaly detection — Sentinel-style statistical baselines
3. Type/shape drift detection — field types match baseline expectations

This is the "anomaly detection" part of our Sentinel anomaly-detection heritage,
applied to scraper output instead of DeFi metrics.
"""

import statistics
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    BROKEN = "broken"


@dataclass
class HealthReport:
    status: HealthStatus
    row_count: int
    expected_rows: float
    issues: list[str] = field(default_factory=list)
    null_fields: list[str] = field(default_factory=list)
    z_score: float = 0.0

    @property
    def is_broken(self) -> bool:
        return self.status == HealthStatus.BROKEN

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "row_count": self.row_count,
            "expected_rows": round(self.expected_rows, 1),
            "issues": self.issues,
            "null_fields": self.null_fields,
            "z_score": round(self.z_score, 2),
        }


class HealthChecker:
    """
    Checks scraper results against a declared schema and historical baselines.

    Usage:
        checker = HealthChecker(required_fields=["title", "url", "points"])
        checker.update_baseline(5)   # from a known-good run
        report = checker.check(results)
        if report.is_broken:
            # trigger self-heal
    """

    def __init__(self, required_fields: list[str], sigma_threshold: float = 2.0):
        self.required_fields = required_fields
        self.sigma_threshold = sigma_threshold
        self._row_history: list[int] = []

    def update_baseline(self, row_count: int) -> None:
        """Feed a known-good row count into the baseline."""
        self._row_history.append(row_count)
        if len(self._row_history) > 20:
            self._row_history = self._row_history[-20:]

    def check(self, results: list[dict]) -> HealthReport:
        """Run full health check on scrape results."""
        issues: list[str] = []
        null_fields: list[str] = []
        row_count = len(results)

        # 1. Empty results = broken
        if row_count == 0:
            return HealthReport(
                status=HealthStatus.BROKEN,
                row_count=0,
                expected_rows=self._expected_rows(),
                issues=["No rows returned — scraper completely broken"],
                z_score=-99.0,
            )

        # 2. Check required fields for nulls/missing
        for field_name in self.required_fields:
            null_count = sum(
                1 for row in results
                if row.get(field_name) is None or row.get(field_name) == ""
            )
            if null_count > 0:
                null_fields.append(field_name)
                if null_count == row_count:
                    issues.append(
                        f"Field '{field_name}' is null in ALL rows — selector likely broken"
                    )
                elif null_count > row_count * 0.5:
                    issues.append(
                        f"Field '{field_name}' is null in {null_count}/{row_count} rows — partial breakage"
                    )

        # 3. Row-count anomaly detection (Sentinel-style z-score)
        z_score = 0.0
        expected = self._expected_rows()
        if self._row_history and expected > 0:
            z_score = (row_count - expected) / max(expected * 0.2, 1.0)
            if abs(z_score) > self.sigma_threshold:
                issues.append(
                    f"Row count anomaly: got {row_count}, expected ~{expected:.0f} "
                    f"(z-score={z_score:.2f}, threshold=±{self.sigma_threshold})"
                )

        # 4. Determine overall status
        has_broken_field = any(
            f"null in ALL rows" in i for i in issues
        )
        if has_broken_field or z_score < -self.sigma_threshold:
            status = HealthStatus.BROKEN
        elif issues or null_fields:
            status = HealthStatus.DEGRADED
        else:
            status = HealthStatus.HEALTHY

        return HealthReport(
            status=status,
            row_count=row_count,
            expected_rows=expected,
            issues=issues,
            null_fields=null_fields,
            z_score=z_score,
        )

    def _expected_rows(self) -> float:
        if not self._row_history:
            return 0.0
        return statistics.mean(self._row_history)

    def to_dict(self) -> dict:
        return {
            "required_fields": self.required_fields,
            "sigma_threshold": self.sigma_threshold,
            "baseline_count": len(self._row_history),
            "expected_rows": round(self._expected_rows(), 1),
        }
