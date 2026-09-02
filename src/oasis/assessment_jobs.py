"""Asynchronous execution boundary for confirmed assessment plans."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path

from oasis.assessment import load_assessment_plan
from oasis.competition_package import generate_competition_package
from oasis.models.analysis import AnalysisRunSummary, PriorityWeights
from oasis.models.assessment import (
    AssessmentJob,
    AssessmentPreferences,
    ExecutionStep,
    RecoveryRecord,
)
from oasis.models.map_conversation import (
    MapAgentResponse,
    MapEvent,
    MapSessionState,
    RiskReport,
    RiskReportEvidence,
)
from oasis.runtime import build_analysis_service
from oasis.risk_reporting import build_priority_risk_report
from oasis.settings import Settings


class AssessmentCoordinator:
    """Run confirmed plans in the background and expose durable progress."""

    def __init__(self) -> None:
        self._jobs: dict[str, AssessmentJob] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def start(
        self,
        *,
        plan_id: str,
        preferences: AssessmentPreferences,
        state: MapSessionState,
        settings: Settings,
    ) -> AssessmentJob:
        plan = load_assessment_plan(settings.core_analyst_analysis_output_dir, plan_id)
        job = AssessmentJob(
            plan_id=plan_id,
            preferences=preferences,
            steps=[step.model_copy(deep=True) for step in plan.steps],
        )
        self._jobs[job.job_id] = job
        self._persist(job, settings)
        task = asyncio.create_task(
            self._execute(
                job.job_id,
                plan.question,
                plan.intent.category,
                plan.reusable_run_id,
                state,
                settings,
            )
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    def get(self, job_id: str, settings: Settings) -> AssessmentJob:
        if job_id in self._jobs:
            return self._jobs[job_id]
        path = self._job_path(job_id, settings)
        if not path.is_file():
            raise ValueError(f"Unknown assessment job id: {job_id}")
        job = AssessmentJob.model_validate_json(path.read_text(encoding="utf-8"))
        self._jobs[job_id] = job
        return job

    async def _execute(
        self,
        job_id: str,
        question: str,
        intent_category: str,
        reusable_run_id: str | None,
        state: MapSessionState,
        settings: Settings,
    ) -> None:
        job = self._jobs[job_id]
        recovery: list[RecoveryRecord] = []
        service = build_analysis_service(settings)
        try:
            job.status = "running"
            self._start_step(job, "data_readiness", "Checking registered datasets and configured APIs.")
            readiness = await service.data_readiness()
            self._finish_step(
                job,
                "data_readiness",
                "warning" if readiness.incomplete else "completed",
                f"{len(readiness.available)} available; {len(readiness.incomplete)} incomplete.",
            )
            self._persist(job, settings)

            self._start_step(job, "hazard", "Executing the confirmed deterministic scenario.")
            if intent_category == "historical_validation" or job.preferences.scenario == "historical":
                result = await service.run_historical_validation(
                    issue_time=job.preferences.historical_issue_time
                    or datetime(2023, 10, 6, 6, tzinfo=UTC),
                    forecast_horizon=job.preferences.forecast_horizon_hours,
                    hazard_threshold=job.preferences.hazard_threshold,
                    priority_scenario=(
                        job.preferences.priority_scenario
                        if job.preferences.priority_scenario != "custom"
                        else "social_equity"
                    ),
                )
                self._finish_historical_steps(job, result)
                quality = self._historical_quality(result)
                quality_path = self._write_quality_file(job, quality, settings)
                service.attach_run_artifacts(result.run_id, {"quality_report": str(quality_path)})
                source_run_id = result.run_id
            else:
                reusable_source = self._compatible_reusable_source(
                    service, reusable_run_id, job.preferences
                )
                if reusable_source:
                    self._finish_step(job, "hazard", "completed", "Reused the compatible saved Hazard result.")
                    self._finish_step(job, "exposure", "completed", "Reused saved exposure components.")
                    self._finish_step(job, "vulnerability", "completed", "Reused saved vulnerability components.")
                    self._start_step(job, "priority", "Re-ranking persisted Data Zone components.")
                    result = await service.rerank_flood_priority(
                        source_run_id=reusable_source,
                        weights=job.preferences.weights,
                        include_simd=job.preferences.include_simd,
                        scenario_name=job.preferences.priority_scenario,
                    )
                    self._finish_step(
                        job,
                        "priority",
                        "completed",
                        "Re-ranked without calling a weather API or re-running Hazard.",
                    )
                    source_run_id = reusable_source
                else:
                    result = await service.run_flood_priority_assessment(
                        scenario=job.preferences.scenario,
                        use_live_data=job.preferences.use_live_data,
                        forecast_horizon=job.preferences.forecast_horizon_hours,
                        hazard_threshold=job.preferences.hazard_threshold,
                        priority_scenario=(
                            job.preferences.priority_scenario
                            if job.preferences.priority_scenario != "custom"
                            else "social_equity"
                        ),
                    )
                    source_run_id = result.run_id
                    self._finish_spatial_steps(job, result)
                    if self._needs_rerank(job.preferences):
                        self._start_step(job, "priority", "Re-ranking persisted Data Zone components.")
                        result = await service.rerank_flood_priority(
                            source_run_id=result.run_id,
                            weights=job.preferences.weights,
                            include_simd=job.preferences.include_simd,
                            scenario_name=job.preferences.priority_scenario,
                        )
                        self._finish_step(
                            job,
                            "priority",
                            "completed",
                            "Re-ranked without calling a weather API or re-running Hazard.",
                        )
                job.status = "validating"
                self._start_step(job, "validation", "Running deterministic quality gates.")
                quality = await service.validate_flood_priority_run(
                    result.run_id,
                    expected_data_zone_count=1071,
                    analysis_time=datetime.now(UTC),
                )
                quality_step_status = "failed" if quality["status"] == "fail" else (
                    "warning" if quality["status"] == "warning" else "completed"
                )
                self._finish_step(
                    job,
                    "validation",
                    quality_step_status,
                    f"Quality gate status: {quality['status']}.",
                )

            recovery.extend(self._recovery_from_result(service._load(result.run_id)))

            job.source_run_id = source_run_id
            job.run_id = result.run_id
            self._start_step(job, "publish", "Preparing map layers and audit artifacts.")
            self._finish_step(job, "publish", "completed", "Decision layers and report are ready.")
            trace_path, recovery_path = self._write_audit_files(job, recovery, settings)
            package = generate_competition_package(
                output_root=settings.core_analyst_analysis_output_dir,
                job=job,
                result=result,
                quality=quality,
            )
            service.attach_run_artifacts(
                result.run_id,
                {
                    "execution_trace": str(trace_path),
                    "recovery_log": str(recovery_path),
                    **package,
                },
            )
            response = self._response(
                question,
                state,
                result,
                quality,
                job.preferences,
                service._load,
            )
            response.execution_trace = [step.model_copy(deep=True) for step in job.steps]
            job.final_response = response.model_dump(mode="json")
            if result.status in {"partial", "unavailable", "failed"} or quality["status"] == "fail":
                job.status = "partial" if result.status != "failed" else "failed"
                job.available_actions = [
                    "retry",
                    "run_with_cached_inputs",
                    "switch_to_current_or_static_scenario",
                ]
            else:
                job.status = "completed"
            job.warnings = [warning.message for warning in result.warnings]
        except Exception as exc:
            recovery.append(
                RecoveryRecord(
                    step_id=self._running_step(job),
                    action="stop",
                    reason=type(exc).__name__,
                    outcome=str(exc),
                )
            )
            self._fail_running_step(job, str(exc))
            job.status = "failed"
            job.error = str(exc)
            job.available_actions = [
                "retry",
                "run_with_cached_inputs",
                "switch_to_current_or_static_scenario",
            ]
            self._write_audit_files(job, recovery, settings)
        finally:
            job.updated_at = datetime.now(UTC)
            self._persist(job, settings)

    @staticmethod
    def _needs_rerank(preferences: AssessmentPreferences) -> bool:
        presets = {
            "life_safety": PriorityWeights(hazard=0.45, exposure=0.40, vulnerability=0.15),
            "social_equity": PriorityWeights(hazard=0.25, exposure=0.25, vulnerability=0.50),
            "economic_protection": PriorityWeights(hazard=0.40, exposure=0.45, vulnerability=0.15),
        }
        expected = presets.get(preferences.priority_scenario)
        return not preferences.include_simd or expected is None or preferences.weights != expected

    @staticmethod
    def _compatible_reusable_source(
        service,
        run_id: str | None,
        preferences: AssessmentPreferences,
    ) -> str | None:
        if not run_id:
            return None
        try:
            payload = service._load(run_id)
        except ValueError:
            return None
        if payload.get("analysis_type") == "priority_rerank":
            run_id = str(payload.get("provenance", {}).get("source_run_id", ""))
            if not run_id:
                return None
            payload = service._load(run_id)
        if payload.get("analysis_type") != "flood_priority_assessment":
            return None
        summary = payload.get("summary", {})
        provenance = payload.get("provenance", {})
        matches = (
            summary.get("scenario") == preferences.scenario
            and summary.get("hazard_threshold") == preferences.hazard_threshold
            and provenance.get("forecast_horizon_hours") == preferences.forecast_horizon_hours
            and bool(provenance.get("use_live_data")) == preferences.use_live_data
        )
        return run_id if matches else None

    @staticmethod
    def _start_step(job: AssessmentJob, step_id: str, detail: str) -> None:
        step = AssessmentCoordinator._step(job, step_id)
        step.status = "running"
        step.detail = detail
        step.started_at = datetime.now(UTC)
        job.updated_at = datetime.now(UTC)

    @staticmethod
    def _finish_step(job: AssessmentJob, step_id: str, status: str, detail: str) -> None:
        step = AssessmentCoordinator._step(job, step_id)
        step.status = status
        step.detail = detail
        step.finished_at = datetime.now(UTC)
        job.updated_at = datetime.now(UTC)

    @staticmethod
    def _step(job: AssessmentJob, step_id: str) -> ExecutionStep:
        return next(step for step in job.steps if step.id == step_id)

    @staticmethod
    def _running_step(job: AssessmentJob) -> str:
        return next((step.id for step in job.steps if step.status == "running"), "job")

    @staticmethod
    def _fail_running_step(job: AssessmentJob, detail: str) -> None:
        for step in job.steps:
            if step.status == "running":
                step.status = "failed"
                step.detail = detail
                step.finished_at = datetime.now(UTC)

    def _finish_spatial_steps(self, job: AssessmentJob, result: AnalysisRunSummary) -> None:
        status = "warning" if result.status in {"partial", "unavailable"} else "completed"
        self._finish_step(job, "hazard", status, "Combined hazard scenario completed.")
        self._finish_step(job, "exposure", status, "Population, building and facility exposure aggregated.")
        self._finish_step(job, "vulnerability", status, "Social and accessibility vulnerability aggregated.")
        self._finish_step(job, "priority", status, "Initial priority ranking completed.")

    def _finish_historical_steps(self, job: AssessmentJob, result: AnalysisRunSummary) -> None:
        status = "warning" if result.status in {"partial", "unavailable"} else "completed"
        self._finish_step(job, "hazard", status, "Archived forecast input processed.")
        self._finish_step(job, "exposure", status, "Historical observations reconstructed.")
        self._finish_step(job, "vulnerability", status, "Data Zone components assessed.")
        self._finish_step(job, "priority", status, "Forecast and observation rankings compared.")
        self._finish_step(job, "validation", status, "Forecast issue-time leakage gate evaluated.")

    @staticmethod
    def _historical_quality(result: AnalysisRunSummary) -> dict:
        no_leakage = result.summary.get("no_time_leakage") is True
        status = "pass" if no_leakage and result.status == "success" else (
            "warning" if result.status in {"partial", "unavailable"} else "fail"
        )
        return {
            "run_id": result.run_id,
            "status": status,
            "checks": [
                {
                    "code": "forecast_issue_time",
                    "status": "pass" if no_leakage else "fail",
                    "message": "Forecast reference time does not exceed the declared issue time."
                    if no_leakage else "No-leakage validation could not be established.",
                    "evidence": {"no_time_leakage": result.summary.get("no_time_leakage")},
                }
            ],
        }

    def _write_audit_files(
        self,
        job: AssessmentJob,
        recovery: list[RecoveryRecord],
        settings: Settings,
    ) -> tuple[Path, Path]:
        root = settings.core_analyst_analysis_output_dir / "jobs" / job.job_id
        root.mkdir(parents=True, exist_ok=True)
        trace_path = root / "execution_trace.json"
        recovery_path = root / "recovery_log.json"
        trace_path.write_text(
            json.dumps([step.model_dump(mode="json") for step in job.steps], indent=2),
            encoding="utf-8",
        )
        recovery_path.write_text(
            json.dumps([item.model_dump(mode="json") for item in recovery], indent=2),
            encoding="utf-8",
        )
        return trace_path, recovery_path

    @staticmethod
    def _recovery_from_result(payload: object) -> list[RecoveryRecord]:
        records: list[RecoveryRecord] = []

        def visit(value: object) -> None:
            if isinstance(value, dict):
                if value.get("action") in {"retry", "reuse_cache", "degrade", "stop"}:
                    records.append(
                        RecoveryRecord(
                            step_id="data_readiness",
                            action=value["action"],
                            reason=str(value.get("status_code") or value.get("reason") or "external_data"),
                            outcome=str(value.get("outcome", "recorded")),
                        )
                    )
                diagnostics = value.get("dynamic_data_diagnostics")
                if isinstance(diagnostics, dict) and diagnostics.get("attempts", 1) > 1:
                    records.append(
                        RecoveryRecord(
                            step_id="hazard",
                            action="retry",
                            reason=str(diagnostics.get("errors", [])),
                            outcome=str(diagnostics.get("final_status", "unknown")),
                        )
                    )
                for item in value.values():
                    visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)

        visit(payload)
        return records

    @staticmethod
    def _write_quality_file(
        job: AssessmentJob,
        quality: dict,
        settings: Settings,
    ) -> Path:
        root = settings.core_analyst_analysis_output_dir / "jobs" / job.job_id
        root.mkdir(parents=True, exist_ok=True)
        path = root / "quality_report.json"
        path.write_text(json.dumps(quality, indent=2), encoding="utf-8")
        return path

    @staticmethod
    def _response(
        question: str,
        state: MapSessionState,
        result: AnalysisRunSummary,
        quality: dict,
        preferences: AssessmentPreferences,
        load_run,
    ) -> MapAgentResponse:
        state = state.model_copy(deep=True)
        old_visible = set(state.visible_analysis_layer_ids)
        for layer in state.analysis_layers:
            if layer.id in old_visible:
                layer.visible = False
        state.analysis_layers.extend(result.map_layers)
        visible = [layer.id for layer in result.map_layers if layer.visible]
        state.visible_analysis_layer_ids = visible
        state.pending_assessment = None
        state.recent_analysis_run_id = result.run_id
        top = result.summary.get("top_areas", [])
        quality_label = str(quality.get("status", "unknown"))
        if result.analysis_type == "historical_flood_validation":
            report = RiskReport(
                title="Glasgow historical forecast-input validation",
                question=question,
                area="Glasgow",
                time_horizon="6–7 October 2023 historical scenario",
                overall_risk="mixed" if quality_label != "fail" else "unknown",
                summary=(
                    "Deterministic spatial outputs are complete and passed the quality gate."
                    if quality_label == "pass"
                    else f"The deterministic run completed with quality status {quality_label}; review flagged checks before using the ranking."
                ),
                key_findings=[
                    f"Rank {item.get('rank')}: {item.get('name') or item.get('id')}"
                    for item in top[:5]
                ],
                evidence=[
                    RiskReportEvidence(
                        label="Deterministic analysis run",
                        value=result.run_id,
                        source=f"OASIS {result.analysis_type}",
                    ),
                    RiskReportEvidence(
                        label="Quality gate",
                        value=quality_label,
                        source="quality_report.json",
                    ),
                ],
            )
        else:
            report = build_priority_risk_report(
                question=question,
                result=result,
                quality=quality,
                preferences=preferences,
                load_run=load_run,
            )
        state.risk_report = report
        state.last_task = (
            "historical" if result.analysis_type == "historical_flood_validation" else "assessment"
        )
        message = (
            f"Run {result.run_id} finished with quality status {quality_label}. "
            + (
                f"The highest-ranked Data Zone is {top[0].get('name') or top[0].get('id')}."
                if top else "No complete priority ranking was available."
            )
        )
        return MapAgentResponse(
            message=message,
            state=state,
            events=[MapEvent(type="sync_analysis_layers", layer_ids=[layer.id for layer in result.map_layers])],
            tools_used=[result.analysis_type, "validate_spatial_outputs"],
            execution_trace=[],
        )

    @staticmethod
    def _score(value: object) -> str:
        return "unavailable" if value is None else f"{float(value):.3f}"

    def _persist(self, job: AssessmentJob, settings: Settings) -> None:
        path = self._job_path(job.job_id, settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(job.model_dump_json(indent=2), encoding="utf-8")

    @staticmethod
    def _job_path(job_id: str, settings: Settings) -> Path:
        if len(job_id) != 12 or any(character not in "0123456789abcdef" for character in job_id):
            raise ValueError("Invalid assessment job id")
        return settings.core_analyst_analysis_output_dir / "jobs" / job_id / "job.json"


assessment_coordinator = AssessmentCoordinator()
