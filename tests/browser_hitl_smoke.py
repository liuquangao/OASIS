"""Headless browser smoke test for the human-confirmed assessment interaction."""

from __future__ import annotations

import json

import httpx
from playwright.sync_api import Route, sync_playwright


BASE_STATE = {
    "locations": [],
    "visible_location_ids": [],
    "routes": [],
    "visible_route_ids": [],
    "active_location_id": None,
    "hazard_layer_visible": False,
    "analysis_layers": [],
    "visible_analysis_layer_ids": [],
    "risk_report": None,
    "pending_assessment": None,
    "recent_analysis_run_id": None,
    "last_task": None,
}


def fulfill(route: Route, payload: dict) -> None:
    route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))


def steps(status: str) -> list[dict]:
    return [
        {"id": key, "label": label, "status": status, "retry_count": 0}
        for key, label in (
            ("data_readiness", "Data readiness"),
            ("hazard", "Multi-hazard analysis"),
            ("exposure", "Exposure aggregation"),
            ("vulnerability", "Social vulnerability"),
            ("priority", "Priority ranking"),
            ("validation", "Quality validation"),
            ("publish", "Publish decision layers"),
        )
    ]


def main() -> None:
    schema = httpx.get("http://127.0.0.1:8000/openapi.json", timeout=10).json()
    assert "/assessments/{plan_id}/execute" in schema["paths"]
    assert "/assessment-jobs/{job_id}" in schema["paths"]
    assert "/analysis/runs/{run_id}/rerank" in schema["paths"]

    plan = {
        "plan_id": "abcdef123456",
        "status": "awaiting_confirmation",
        "question": "What is the flood risk and social-equity priority across Glasgow for the next 24 hours?",
        "intent": {
            "category": "integrated_risk",
            "area": "Glasgow",
            "scenario": "future",
            "forecast_horizon_hours": 24,
            "hazard_threshold": 2,
            "priority_scenario": "social_equity",
            "rationale": "This request combines flood hazard and social vulnerability.",
            "confidence": 0.98,
        },
        "preferences": {
            "scenario": "future",
            "use_live_data": True,
            "forecast_horizon_hours": 24,
            "hazard_threshold": 2,
            "priority_scenario": "social_equity",
            "weights": {"hazard": 0.25, "exposure": 0.25, "vulnerability": 0.5},
            "include_simd": True,
            "historical_issue_time": None,
        },
        "required_datasets": ["population", "simd"],
        "missing_datasets": ["radar_rainfall"],
        "reusable_run_id": None,
        "steps": steps("pending"),
        "created_at": "2026-09-01T10:00:00Z",
        "requires_confirmation": True,
    }
    pending_state = {**BASE_STATE, "pending_assessment": plan, "last_task": "assessment"}
    layer = {
        "id": "geojson-run-priority",
        "label": "Flood Priority Assessment · Priority By Data Zone",
        "kind": "geojson",
        "url": "http://127.0.0.1:8000/analysis/runs/123456abcdef/artifacts/priority_by_data_zone",
        "layer_name": None,
        "style": "priority",
        "opacity": 0.68,
        "visible": True,
    }
    report = {
        "title": "Glasgow flood risk and social-priority assessment",
        "question": plan["question"],
        "area": "Glasgow",
        "time_horizon": "Confirmed future 24-hour scenario",
        "overall_risk": "mixed",
        "summary": "Deterministic spatial outputs passed the quality gate.",
        "key_findings": ["Rank 1: Test Data Zone — H 0.40, E 0.60, V 0.80."],
        "evidence": [],
        "limitations": [],
        "generated_at": "2026-09-01T10:01:00Z",
    }
    final_state = {
        **BASE_STATE,
        "analysis_layers": [layer],
        "visible_analysis_layer_ids": [layer["id"]],
        "risk_report": report,
        "recent_analysis_run_id": "123456abcdef",
        "last_task": "assessment",
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            executable_path="/usr/bin/google-chrome",
        )
        page = browser.new_page(viewport={"width": 1500, "height": 960})
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        page.route(
            "**/setup/*",
            lambda route: fulfill(
                route,
                {
                    "can_use_agent": True,
                    "configuration": [
                        {
                            "id": "agent_model",
                            "label": "Agent model",
                            "configured": True,
                            "importance": "required",
                            "message": "Configured",
                            "environment_variables": [],
                        }
                    ],
                    "data": {"status": "complete", "checked_files": 16, "profile": "glasgow-5m-exact", "automatic_action": None},
                    "job": {"state": "idle", "action": None, "error": None},
                },
            ),
        )
        page.route(
            "**/agent/turn",
            lambda route: fulfill(
                route,
                {
                    "message": "Review the proposed decision settings, then confirm.",
                    "state": pending_state,
                    "events": [],
                    "tools_used": ["route_analysis_intent", "get_core_analysis_data_readiness"],
                    "pending_assessment": plan,
                    "execution_trace": plan["steps"],
                },
            ),
        )
        page.route(
            "**/assessments/*/execute",
            lambda route: fulfill(
                route,
                {
                    "job_id": "fedcba654321",
                    "plan_id": plan["plan_id"],
                    "status": "queued",
                    "preferences": plan["preferences"],
                    "steps": steps("pending"),
                    "warnings": [],
                    "created_at": "2026-09-01T10:00:01Z",
                    "updated_at": "2026-09-01T10:00:01Z",
                },
            ),
        )
        page.route(
            "**/assessment-jobs/*",
            lambda route: fulfill(
                route,
                {
                    "job_id": "fedcba654321",
                    "plan_id": plan["plan_id"],
                    "status": "completed",
                    "preferences": plan["preferences"],
                    "steps": steps("completed"),
                    "run_id": "123456abcdef",
                    "source_run_id": "123456abcdef",
                    "warnings": [],
                    "final_response": {
                        "message": "Run 123456abcdef finished with quality status pass.",
                        "state": final_state,
                        "events": [{"type": "sync_analysis_layers", "layer_ids": [layer["id"]], "location_ids": [], "route_ids": [], "visible": None}],
                        "tools_used": ["flood_priority_assessment", "validate_spatial_outputs"],
                        "pending_assessment": None,
                        "execution_trace": steps("completed"),
                    },
                    "created_at": "2026-09-01T10:00:01Z",
                    "updated_at": "2026-09-01T10:01:00Z",
                },
            ),
        )
        page.route(
            "**/analysis/runs/*/artifacts/*",
            lambda route: fulfill(route, {"type": "FeatureCollection", "features": []}),
        )
        page.route(
            "**/analysis/runs/*/rerank",
            lambda route: fulfill(
                route,
                {
                    "run": {
                        "run_id": "789abc456def",
                        "analysis_type": "priority_rerank",
                        "status": "success",
                        "summary": {"top_areas": [{"id": "S01000001", "name": "Test Data Zone", "rank": 1, "rank_change": 4}]},
                        "output_keys": ["priority_by_data_zone"],
                        "map_layers": [{**layer, "id": "geojson-rerank-priority"}],
                        "warnings": [],
                        "requires_human_review": True,
                    },
                    "quality": {"status": "pass", "checks": []},
                },
            ),
        )

        page.goto("http://127.0.0.1:3000/", wait_until="domcontentloaded")
        page.locator("#agent-input").wait_for(state="visible")
        page.locator("#agent-input").fill(plan["question"])
        page.locator("#agent-form button").click()
        page.locator("#assessment-decision").wait_for(state="visible")
        assert "confirmation" in page.locator("#assessment-status-badge").inner_text().lower()
        assert page.locator("#weight-total").inner_text() == "Total 100%"

        page.locator("#assessment-confirm").click()
        page.get_by_text("Run 123456abcdef finished with quality status pass.").wait_for()
        assert page.locator("#assessment-confirm").inner_text() == "Apply re-ranking"
        assert page.locator("#analysis-layer-list .action-button").count() == 1

        for selector, value in (
            ("#hazard-weight", "0.20"),
            ("#exposure-weight", "0.20"),
            ("#vulnerability-weight", "0.60"),
        ):
            page.locator(selector).evaluate(
                "(element, value) => { element.value = value; element.dispatchEvent(new Event('input', { bubbles: true })); }",
                value,
            )
        assert page.locator("#weight-total").inner_text() == "Total 100%"
        page.locator("#assessment-confirm").click()
        page.get_by_text("Re-ranked without weather API calls.").wait_for()
        assert page.locator("#analysis-layer-list .action-button").count() == 1
        page.screenshot(path="/tmp/oasis-hitl-browser-smoke.png", full_page=True)
        assert errors == []
        browser.close()


if __name__ == "__main__":
    main()
