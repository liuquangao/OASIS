# OASIS competition demo — 2:45 target

Record a genuine run with cached inputs and edit only dead time. Keep the timer
strictly below three minutes.

## 0:00–0:20 — Frame the decision

Show Glasgow and ask:

> What is the flood risk and social-equity priority across Glasgow for the next 24 hours?

Say: “OASIS separates physical Hazard, exposed people/assets, social
Vulnerability, and a value-dependent intervention Priority. The language model
does not invent any Data Zone values.”

## 0:20–0:45 — Show agent planning and HITL

The Agent returns an editable plan before computation. Point out:

- semantic intent routing and the filtered toolset;
- class ≥ 2 threshold and 24-hour horizon;
- social-equity weights 0.25 / 0.25 / 0.50;
- missing-data disclosure and the SIMD switch.

Change the hazard threshold once, restore it, then select **Confirm and run**.

## 0:45–1:20 — Execute and audit

Use cached/precomputed weather inputs. Show the live steps: Data readiness,
Hazard, Exposure, Vulnerability, Priority, Validation and Publish. Say: “External
downloads are cached; failures become an explicit partial result. CRS, grid,
feature counts, score ranges, source times, checksums, ranks and anomalous class
distributions are quality-gated.”

## 1:20–1:50 — Decision output

Switch among the four layers, ending on Priority. Open the Risk report and show:

- the Top Data Zones;
- each area's Hazard, Exposure and Vulnerability components;
- the quality status and provenance run ID;
- the four-panel map and Top-10/sensitivity artifacts.

Avoid describing Priority as flood probability or an official warning.

## 1:50–2:15 — Stakeholder trade-off

Increase Vulnerability weight and reduce Hazard/Exposure so the total remains
100%, then apply re-ranking. Show rank changes. Say: “This takes the saved Data
Zone components, makes zero weather API calls, and does not rerun Hazard. It
exposes rather than hides the stakeholder value judgement.”

## 2:15–2:40 — Historical validation and recovery

Open the cached 6–7 October 2023 validation artifacts. Show rainfall comparison,
Top-10 overlap/rank correlation and baseline-versus-event hazard distribution.
Say: “The code rejects forecasts issued after 06:00 on 6 October. Because there
is no independent Glasgow inundation footprint, this validates forecast inputs
and decision stability, not flood-pixel accuracy.”

Briefly show a recorded missing-CEDA or HTTP-retry recovery trace and its suggested
actions: retry, use cache, or choose a current/static scenario.

## 2:40–2:45 — Close

“OASIS is complete for Glasgow today. Its adapters and alignment checks are
transferable, but we do not claim that a second city is already operational.”
