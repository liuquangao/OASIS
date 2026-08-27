from __future__ import annotations


class QueryUnderstanding:
    """Placeholder boundary for a future LLM or rule-based agent."""

    def parse(self, query: str) -> dict[str, object]:
        normalized = query.lower()
        if "rainfall" in normalized and ("increase" in normalized or "%" in normalized):
            return {
                "hazard_type": "pluvial",
                "workflow": "pluvial_flood",
                "scenario": "rainfall_multiplier",
                "rainfall_multiplier": 1.2,
            }
        return {
            "hazard_type": "pluvial",
            "workflow": "pluvial_flood",
            "scenario": "current_or_demo",
            "rainfall_multiplier": 1.0,
        }


def explain_result(intent: dict[str, object], metadata: dict[str, object]) -> str:
    return (
        f"Executed {intent['workflow']} using {metadata['analysis_method']} for "
        f"{metadata['hazard_type']} hazard. Outputs are WebGIS-ready GeoTIFF rasters "
        "plus analysis metadata."
    )
