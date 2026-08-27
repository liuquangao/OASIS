from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DiscoveryItem:
    name: str
    source: str
    data_type: str
    status: str
    official_source_url: str
    expected_local_path: str | None = None
    reason: str | None = None


class DataDiscoveryReporter:
    """Developer-only data discovery status reporter.

    This layer never fabricates downloads or API success. It only reports local
    readiness and what a future acquisition agent would need to do.
    """

    def __init__(self, catalogue_path: str | Path = "data_catalogue.json"):
        self.catalogue_path = Path(catalogue_path)

    def discover(self) -> list[DiscoveryItem]:
        catalogue = json.loads(self.catalogue_path.read_text(encoding="utf-8"))
        items: list[DiscoveryItem] = []
        for entry in catalogue:
            expected = entry.get("expected_local_path")
            local_ready = bool(expected and Path(expected).exists())
            if local_ready:
                status = "Ready: verified local file exists"
                reason = None
            elif entry["type"] == "real_time":
                status = "Prototype only: mock source available; live API not verified"
                reason = "Offline MVP does not call live official APIs by default."
            elif entry["type"] == "static_reference":
                status = "Reference only for MVP"
                reason = "Not required by the current pluvial weighted-overlay workflow."
            else:
                status = "Manual download or Component 1 preprocessing required"
                reason = (
                    "Core Analyst requires standardized aligned GeoTIFFs; source discovery/download "
                    "and preprocessing are human-led or Component 1 responsibilities in this demo."
                )
            items.append(
                DiscoveryItem(
                    name=entry["name"],
                    source=entry["source"],
                    data_type=entry["type"],
                    status=status,
                    official_source_url=entry.get("official_source_url", ""),
                    expected_local_path=expected,
                    reason=reason,
                )
            )
        return items

    def format_report(self) -> str:
        lines = [
            "========================================",
            "FLOOD HAZARD DATA DISCOVERY",
            "========================================",
            "",
            "Developer/debug-only report. This is not part of the end-user interface.",
            "No dataset is marked downloaded unless a local file was verified.",
            "",
        ]
        for item in self.discover():
            marker = "[OK]" if item.status.startswith("Ready") else "[!]"
            lines.extend(
                [
                    f"{marker} {item.name}",
                    f"    Source: {item.source}",
                    f"    Type: {item.data_type}",
                    f"    Status: {item.status}",
                    f"    Official Source: {item.official_source_url}",
                ]
            )
            if item.expected_local_path:
                lines.append(f"    Expected local path: {item.expected_local_path}")
            if item.reason:
                lines.append(f"    Reason: {item.reason}")
            lines.append("")
        lines.append("========================================")
        return "\n".join(lines)
