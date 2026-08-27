"""Minimal structured output returned by the OASIS Agent."""

from __future__ import annotations

from pydantic import BaseModel, Field


class EvidenceReference(BaseModel):
    label: str
    source_url: str
    observation_time: str | None = None


class AgentOutput(BaseModel):
    answer: str
    evidence: list[EvidenceReference] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    requires_human_review: bool = True

