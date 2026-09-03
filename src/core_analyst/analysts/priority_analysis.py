from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


COMPONENTS = ("hazard", "exposure", "vulnerability")


@dataclass
class PriorityScenario:
    name: str
    weights: dict[str, float]
    description: str = ""


class PriorityInputError(ValueError):
    """Raised when priority analysis receives invalid deterministic inputs."""


class PriorityAnalysisEngine:
    """Deterministic priority, trade-off, and sensitivity analysis."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = (config or {}).get("priority_analysis", {})

    def run_priority_analysis(
        self,
        *,
        units: list[dict[str, Any]] | None = None,
        hazard_result: dict[str, Any] | None = None,
        exposure_result: dict[str, Any] | None = None,
        vulnerability_result: dict[str, Any] | None = None,
        scenario: str | dict[str, Any] = "custom",
        weights: dict[str, float] | None = None,
        output_dir: str | Path = "outputs/priority",
        top_n: int | None = None,
    ) -> dict[str, Any]:
        warnings: list[dict[str, str]] = []
        priority_scenario = self._scenario(scenario, weights)
        self._validate_weights(priority_scenario.weights)
        top_n = int(top_n if top_n is not None else self.config.get("top_n", 10))
        if top_n <= 0:
            raise PriorityInputError("top_n must be positive.")

        unit_records = self._unit_records(
            units=units,
            hazard_result=hazard_result,
            exposure_result=exposure_result,
            vulnerability_result=vulnerability_result,
            warnings=warnings,
        )
        if not unit_records:
            result = self._unavailable_result(priority_scenario, top_n, hazard_result, exposure_result, vulnerability_result, warnings)
            self._write_json(result, Path(output_dir) / "priority_summary.json", "priority_summary")
            return result

        rankings = []
        incomplete = False
        for unit in unit_records:
            components = {component: unit.get(component) for component in COMPONENTS}
            missing = [name for name, value in components.items() if value is None]
            if missing:
                incomplete = True
                rankings.append(self._incomplete_ranking_row(unit, components, missing))
                warnings.append({
                    "code": "priority_component_missing",
                    "message": f"Unit {unit['id']} is missing components: {missing}.",
                })
                continue
            contributions = {
                f"{component}_contribution": float(components[component]) * float(priority_scenario.weights[component])
                for component in COMPONENTS
            }
            priority_score = float(sum(contributions.values()))
            rankings.append({
                "id": unit["id"],
                "name": unit.get("name"),
                "component_scores": components,
                **contributions,
                "priority_score": priority_score,
                "rank": None,
            })

        complete_rows = [row for row in rankings if row["priority_score"] is not None]
        complete_rows.sort(key=lambda row: (-row["priority_score"], str(row["id"])))
        for rank, row in enumerate(complete_rows, start=1):
            row["rank"] = rank
        incomplete_rows = [row for row in rankings if row["priority_score"] is None]
        rankings = complete_rows + sorted(incomplete_rows, key=lambda row: str(row["id"]))

        status = "partial" if incomplete else "success"
        if not complete_rows:
            status = "unavailable"
        result = {
            "status": status,
            "scenario": priority_scenario.name,
            "weights": priority_scenario.weights,
            "rankings": rankings,
            "top_areas": complete_rows[:top_n],
            "metadata": self._metadata(priority_scenario, top_n),
            "provenance": self._provenance(
                priority_scenario,
                top_n,
                hazard_result,
                exposure_result,
                vulnerability_result,
                "weighted_priority_ranking",
            ),
            "warnings": warnings,
            "outputs": {},
        }
        self._write_json(result, Path(output_dir) / "priority_summary.json", "priority_summary")
        return result

    def compare_priority_scenarios(
        self,
        *,
        scenarios: list[str | dict[str, Any]],
        units: list[dict[str, Any]] | None = None,
        hazard_result: dict[str, Any] | None = None,
        exposure_result: dict[str, Any] | None = None,
        vulnerability_result: dict[str, Any] | None = None,
        output_dir: str | Path = "outputs/priority_comparison",
        top_n: int | None = None,
    ) -> dict[str, Any]:
        warnings: list[dict[str, str]] = []
        scenario_results = [
            self.run_priority_analysis(
                units=units,
                hazard_result=hazard_result,
                exposure_result=exposure_result,
                vulnerability_result=vulnerability_result,
                scenario=scenario,
                output_dir=Path(output_dir) / str(self._scenario(scenario, None).name),
                top_n=top_n,
            )
            for scenario in scenarios
        ]
        rank_comparison = self._rank_comparison(scenario_results)
        result = {
            "status": "success" if any(result["rankings"] for result in scenario_results) else "unavailable",
            "scenarios": [{"name": result["scenario"], "weights": result["weights"], "status": result["status"]} for result in scenario_results],
            "rank_comparison": rank_comparison,
            "metadata": {
                "analysis_method": "priority_scenario_comparison",
                "ranking_method": "priority_score_desc_then_id",
                "purpose": "Expose trade-offs across explicit decision assumptions.",
            },
            "provenance": {"scenario_results": [result["provenance"] for result in scenario_results]},
            "warnings": warnings + [warning for result in scenario_results for warning in result["warnings"]],
            "outputs": {},
        }
        self._write_json(result, Path(output_dir) / "priority_scenario_comparison.json", "scenario_comparison")
        return result

    def run_sensitivity_analysis(
        self,
        *,
        base_scenario: str | dict[str, Any],
        vary_component: str,
        values: list[float],
        units: list[dict[str, Any]] | None = None,
        hazard_result: dict[str, Any] | None = None,
        exposure_result: dict[str, Any] | None = None,
        vulnerability_result: dict[str, Any] | None = None,
        output_dir: str | Path = "outputs/priority_sensitivity",
        top_n: int | None = None,
    ) -> dict[str, Any]:
        if vary_component not in COMPONENTS:
            raise PriorityInputError(f"vary_component must be one of {COMPONENTS}.")
        warnings: list[dict[str, str]] = []
        base = self._scenario(base_scenario, None)
        self._validate_weights(base.weights)
        scenarios = [
            {
                "name": f"{base.name}_{vary_component}_{value:g}",
                "weights": self._adjust_weight(base.weights, vary_component, float(value)),
                "description": f"Sensitivity run varying {vary_component} weight.",
            }
            for value in values
        ]
        scenario_results = [
            self.run_priority_analysis(
                units=units,
                hazard_result=hazard_result,
                exposure_result=exposure_result,
                vulnerability_result=vulnerability_result,
                scenario=scenario,
                output_dir=Path(output_dir) / scenario["name"],
                top_n=top_n,
            )
            for scenario in scenarios
        ]
        base_result = scenario_results[0] if scenario_results else None
        sensitivity_rows = self._sensitivity_rows(base_result, scenario_results, top_n or self.config.get("top_n", 10))
        result = {
            "status": "success" if scenario_results else "unavailable",
            "sensitivity": {
                "vary_component": vary_component,
                "values": values,
                "rank_changes": sensitivity_rows,
                "top_n_changes": self._top_n_changes(scenario_results, top_n or self.config.get("top_n", 10)),
            },
            "scenarios": [{"name": result["scenario"], "weights": result["weights"], "status": result["status"]} for result in scenario_results],
            "metadata": {
                "analysis_method": "decision_sensitivity_analysis",
                "interpretation": "Decision sensitivity analysis, not probabilistic uncertainty estimation.",
            },
            "provenance": {
                "base_scenario": base.__dict__,
                "sensitivity_parameters": {"vary_component": vary_component, "values": values, "top_n": top_n},
                "scenario_results": [result["provenance"] for result in scenario_results],
            },
            "warnings": warnings + [warning for result in scenario_results for warning in result["warnings"]],
            "outputs": {},
        }
        self._write_json(result, Path(output_dir) / "priority_sensitivity.json", "sensitivity_summary")
        return result

    def _scenario(self, scenario: str | dict[str, Any], weights: dict[str, float] | None) -> PriorityScenario:
        if isinstance(scenario, dict):
            scenario_weights = dict(scenario.get("weights", {}))
            if weights is not None:
                scenario_weights = weights
            return PriorityScenario(str(scenario.get("name", "custom")), scenario_weights, str(scenario.get("description", "")))
        if weights is not None:
            return PriorityScenario(str(scenario), weights, "Custom explicit weights.")
        configured = self.config.get("scenarios", {}).get(str(scenario))
        if configured:
            return PriorityScenario(str(scenario), dict(configured["weights"]), str(configured.get("description", "")))
        raise PriorityInputError(f"Unknown priority scenario {scenario}; provide weights or configure it.")

    def _validate_weights(self, weights: dict[str, float]) -> None:
        if set(weights) != set(COMPONENTS):
            raise PriorityInputError(f"Weights must include exactly {COMPONENTS}.")
        for name, value in weights.items():
            if float(value) < 0.0 or float(value) > 1.0:
                raise PriorityInputError(f"Weight {name} must be between 0 and 1.")
        if not np.isclose(sum(float(value) for value in weights.values()), 1.0):
            raise PriorityInputError("Priority weights must sum to 1.")

    def _unit_records(
        self,
        *,
        units: list[dict[str, Any]] | None,
        hazard_result: dict[str, Any] | None,
        exposure_result: dict[str, Any] | None,
        vulnerability_result: dict[str, Any] | None,
        warnings: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        if units is not None:
            return [self._coerce_unit(unit) for unit in units]
        if vulnerability_result is None or vulnerability_result.get("status") == "unavailable":
            warnings.append({"code": "vulnerability_unavailable", "message": "Vulnerability result is required for unit-level priority ranking."})
            return []
        hazard_proxy = self._hazard_proxy(hazard_result)
        exposure_proxy = self._exposure_proxy(exposure_result)
        records = []
        for unit in vulnerability_result.get("units", []):
            vulnerability_proxy = unit.get("composite_vulnerability_proxy")
            if vulnerability_proxy is None:
                profile = unit.get("vulnerability_profile", {})
                values = [float(value) for value in profile.values() if value is not None]
                vulnerability_proxy = None if not values else float(np.mean(values))
            records.append({
                "id": str(unit.get("id")),
                "name": unit.get("name"),
                "hazard": hazard_proxy,
                "exposure": exposure_proxy,
                "vulnerability": vulnerability_proxy,
            })
        return records

    def _coerce_unit(self, unit: dict[str, Any]) -> dict[str, Any]:
        component_scores = unit.get("component_scores", {})
        return {
            "id": str(unit.get("id")),
            "name": unit.get("name"),
            "hazard": unit.get("hazard", component_scores.get("hazard")),
            "exposure": unit.get("exposure", component_scores.get("exposure")),
            "vulnerability": unit.get("vulnerability", component_scores.get("vulnerability")),
        }

    def _hazard_proxy(self, hazard_result: dict[str, Any] | None) -> float | None:
        if not hazard_result or hazard_result.get("status") == "unavailable":
            return None
        summary = hazard_result.get("summary", {})
        for key in ("hazard_score", "mean_hazard", "hazard_proxy"):
            if summary.get(key) is not None:
                return float(summary[key])
        return None if hazard_result.get("hazard_score") is None else float(hazard_result["hazard_score"])

    def _exposure_proxy(self, exposure_result: dict[str, Any] | None) -> float | None:
        if not exposure_result or exposure_result.get("status") == "unavailable":
            return None
        summary = exposure_result.get("summary", {})
        values = []
        for key in ("population", "buildings"):
            value = summary.get(key, {}).get("exposure_ratio")
            if value is not None:
                values.append(float(value))
        critical = summary.get("critical_infrastructure", {})
        if critical.get("total"):
            values.append(float(critical.get("exposed", 0)) / float(critical["total"]))
        return None if not values else float(np.mean(values))

    def _incomplete_ranking_row(self, unit: dict[str, Any], components: dict[str, Any], missing: list[str]) -> dict[str, Any]:
        return {
            "id": unit["id"],
            "name": unit.get("name"),
            "component_scores": components,
            "hazard_contribution": None,
            "exposure_contribution": None,
            "vulnerability_contribution": None,
            "priority_score": None,
            "rank": None,
            "missing_components": missing,
        }

    def _rank_comparison(self, scenario_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        unit_ids = sorted({row["id"] for result in scenario_results for row in result.get("rankings", [])})
        comparison = []
        for unit_id in unit_ids:
            row: dict[str, Any] = {"id": unit_id}
            ranks = []
            for result in scenario_results:
                match = next((item for item in result.get("rankings", []) if item["id"] == unit_id), None)
                rank = match.get("rank") if match else None
                row[f"{result['scenario']}_rank"] = rank
                row[f"{result['scenario']}_score"] = match.get("priority_score") if match else None
                if rank is not None:
                    ranks.append(rank)
            row["rank_range"] = None if not ranks else max(ranks) - min(ranks)
            comparison.append(row)
        return comparison

    def _adjust_weight(self, base_weights: dict[str, float], component: str, value: float) -> dict[str, float]:
        if value < 0.0 or value > 1.0:
            raise PriorityInputError("Sensitivity weight values must be between 0 and 1.")
        remaining_components = [name for name in COMPONENTS if name != component]
        remaining_total = sum(float(base_weights[name]) for name in remaining_components)
        weights = {component: value}
        if np.isclose(remaining_total, 0.0):
            share = (1.0 - value) / len(remaining_components)
            weights.update({name: share for name in remaining_components})
        else:
            scale = (1.0 - value) / remaining_total
            weights.update({name: float(base_weights[name]) * scale for name in remaining_components})
        self._validate_weights(weights)
        return weights

    def _sensitivity_rows(self, base_result: dict[str, Any] | None, scenario_results: list[dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
        if base_result is None:
            return []
        base_by_id = {row["id"]: row for row in base_result.get("rankings", [])}
        rows = []
        for result in scenario_results:
            for row in result.get("rankings", []):
                base = base_by_id.get(row["id"])
                rows.append({
                    "scenario": result["scenario"],
                    "id": row["id"],
                    "rank": row.get("rank"),
                    "rank_change_from_base": None if base is None or row.get("rank") is None or base.get("rank") is None else row["rank"] - base["rank"],
                    "score": row.get("priority_score"),
                    "score_change_from_base": None if base is None or row.get("priority_score") is None or base.get("priority_score") is None else row["priority_score"] - base["priority_score"],
                    "in_top_n": row.get("rank") is not None and row["rank"] <= top_n,
                })
        return rows

    def _top_n_changes(self, scenario_results: list[dict[str, Any]], top_n: int) -> dict[str, Any]:
        top_sets = {
            result["scenario"]: {row["id"] for row in result.get("top_areas", [])[:top_n]}
            for result in scenario_results
        }
        if not top_sets:
            return {}
        first_name = next(iter(top_sets))
        base = top_sets[first_name]
        return {
            name: {
                "entered_top_n": sorted(values - base),
                "left_top_n": sorted(base - values),
            }
            for name, values in top_sets.items()
        }

    def _metadata(self, scenario: PriorityScenario, top_n: int) -> dict[str, Any]:
        return {
            "analysis_method": "weighted_priority_analysis",
            "terminology": ["priority_score", "scenario_specific_priority", "decision_oriented_ranking"],
            "priority_score_is_value_dependent": True,
            "scenario_description": scenario.description,
            "ranking_method": "priority_score_desc_then_id",
            "top_n": top_n,
            "formula": "priority = wh * hazard + we * exposure + wv * vulnerability",
        }

    def _provenance(
        self,
        scenario: PriorityScenario,
        top_n: int,
        hazard_result: dict[str, Any] | None,
        exposure_result: dict[str, Any] | None,
        vulnerability_result: dict[str, Any] | None,
        operation: str,
    ) -> dict[str, Any]:
        return {
            "operation": operation,
            "scenario": scenario.name,
            "weights": scenario.weights,
            "top_n": top_n,
            "normalisation": "input component scores are expected on 0-1 scale",
            "hazard_result": self._upstream_provenance(hazard_result),
            "exposure_result": self._upstream_provenance(exposure_result),
            "vulnerability_result": self._upstream_provenance(vulnerability_result),
        }

    def _upstream_provenance(self, result: dict[str, Any] | None) -> dict[str, Any]:
        if not result:
            return {"status": "unavailable"}
        return {
            "status": result.get("status"),
            "summary": result.get("summary", {}),
            "metadata": result.get("metadata", {}),
            "provenance": result.get("provenance", {}),
            "warnings": result.get("warnings", []),
        }

    def _unavailable_result(
        self,
        scenario: PriorityScenario,
        top_n: int,
        hazard_result: dict[str, Any] | None,
        exposure_result: dict[str, Any] | None,
        vulnerability_result: dict[str, Any] | None,
        warnings: list[dict[str, str]],
    ) -> dict[str, Any]:
        return {
            "status": "unavailable",
            "scenario": scenario.name,
            "weights": scenario.weights,
            "rankings": [],
            "top_areas": [],
            "metadata": self._metadata(scenario, top_n),
            "provenance": self._provenance(scenario, top_n, hazard_result, exposure_result, vulnerability_result, "weighted_priority_ranking"),
            "warnings": warnings,
            "outputs": {},
        }

    def _write_json(self, result: dict[str, Any], path: Path, output_key: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        result["outputs"][output_key] = str(path)
        path.write_text(json.dumps(result, indent=2), encoding="utf-8")


def run_priority_analysis(**kwargs: Any) -> dict[str, Any]:
    config = kwargs.pop("config", None)
    return PriorityAnalysisEngine(config).run_priority_analysis(**kwargs)


def compare_priority_scenarios(**kwargs: Any) -> dict[str, Any]:
    config = kwargs.pop("config", None)
    return PriorityAnalysisEngine(config).compare_priority_scenarios(**kwargs)


def run_sensitivity_analysis(**kwargs: Any) -> dict[str, Any]:
    config = kwargs.pop("config", None)
    return PriorityAnalysisEngine(config).run_sensitivity_analysis(**kwargs)
