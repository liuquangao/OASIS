# Competition evidence and paper checklist

## Tool-Use Rigor & Autonomy (25%)

- Natural-language requests are classified into seven typed `AnalysisIntent`
  categories by a compact model pass with thinking disabled for local vLLM.
- `FilteredToolset` exposes only 3–8 relevant tools. The 24-prompt routing suite
  is reproducible with `python scripts/evaluate_intent_routing.py`.
- Startup checks model/API configuration and exact input readiness. Anonymous
  official social-risk sources can be acquired and processed automatically.
- A confirmed job runs deterministic GIS, retries transient downloads twice,
  caches success, and persists `execution_trace.json` and `recovery_log.json`.
- Human confirmation is required before a broad integrated request can call a
  weather service or start computation.

Paper evidence: architecture diagram; execution timeline; one normal trace and
one partial/recovery trace; tool-count table by intent.

## Architectural Robustness & Generalizability (25%)

- Raster and vector joins use explicit CRS transforms and locked 5 m grid
  metadata. SIMD 2011 zones are area-weighted into Census 2022 Data Zones and
  labelled as a cross-version approximation.
- Quality gates cover 1,071-zone completeness, non-empty outputs, score/rank
  invariants, GeoJSON coordinates, raster transform/extent/resolution, source
  time cutoffs, artifact checksums and published-layer presence.
- HTTP 429 respects `Retry-After`; timeout/5xx retries are bounded. Missing
  external data yields `partial`/`unavailable`, never an unlabelled proxy or
  silently redistributed weight.
- AOI, data source and hazard interfaces are configurable. Only Glasgow is
  fully supported; synthetic-area tests demonstrate structure, not a deployed
  second city.

Paper evidence: alignment diagram; failure matrix; quality-report excerpt;
Glasgow-only scope statement.

## Social Good Alignment (25%)

- Hazard = flood intensity/class distribution.
- Exposure = estimated population, intersecting buildings and critical
  facilities in hazardous cells.
- Vulnerability = demographic, socioeconomic/SIMD and hospital/emergency access.
- Priority = explicit weighted intervention ranking, not probability, truth or
  official warning.
- Stakeholders can choose life safety, social equity or economic protection and
  directly edit Hazard/Exposure/Vulnerability weights and SIMD inclusion.
- Every Top Data Zone reports its components and change in rank after re-weighting.

Paper evidence: definitions table; Top-10 component table; weight sensitivity
curve; class 2/class 3 and SIMD on/off comparisons.

## Innovation & Reflection (25%)

- The LLM plans, selects tools and explains evidence; deterministic code owns all
  spatial numbers, ranking and validation.
- The explicit `planned → awaiting_confirmation → queued → running → validating
  → completed/partial/failed` state machine makes autonomy inspectable.
- Fast re-ranking turns normative assumptions into an interactive decision
  dialogue without repeatedly consuming forecast APIs.
- The 6–7 October 2023 experiment enforces issue-time no-leakage and reports
  rainfall bias/MAE/spatial correlation plus Top-10 overlap/rank correlation.
- No inundation truth exists, so flood-pixel IoU/F1 is deliberately omitted.

Paper evidence: responsibility-boundary diagram; historical comparison figure;
ethical limitations and failure cases.

## Four-page paper allocation

1. Problem statement and definitions — 0.65 page.
2. Agent/data/spatial architecture and recovery — 1.15 pages.
3. Results, maps, sensitivity and historical validation — 1.35 pages.
4. Social impact, ethics, limitations and contribution statement — 0.55 page.
5. References and compact appendix material — 0.30 page.

Required figures: four-panel spatial map; compact Agent state/recovery diagram;
one sensitivity curve; one historical-validation panel. Put large machine-readable
tables and traces in the repository rather than the four-page PDF.
