# OASIS guidance for coding agents

## Mission

OASIS combines a browser-based Glasgow flood map, a PydanticAI tool-using
Agent, live observation integrations, and deterministic Core Analyst raster
workflows. Preserve the separation between probabilistic language-model
reasoning and deterministic scientific calculations.

Read `README.md` before changing setup, data, model, or service behavior. Check
`git status` before editing and preserve unrelated user changes.

## Reproduction standard

- The supported data path is the complete Glasgow EPSG:27700 5 m workflow.
- Do not restore or introduce a lower-resolution "quick development" data mode.
- Users provide the licence-gated UKCEH LCM 2019 `gb2019lcm25m.tif` themselves.
  Never download it on their behalf, redistribute it, or commit it.
- Build all other inputs with:

  ```bash
  oasis data rebuild --lcm2019 /path/to/gb2019lcm25m.tif --accept-licences
  oasis data verify
  ```

- A successful verification must report `"ok": true`. Do not treat incomplete,
  demo, or substituted inputs as an exact reproduction.
- Generated inputs, raw caches, analysis outputs, `.env`, and frontend local
  configuration are intentionally Git-ignored.

## Model modes

OASIS supports two real semantic-model modes plus `test` for offline tests:

1. Hosted API: set a PydanticAI model identifier and the provider credential,
   for example `OASIS_MODEL=openai:gpt-5-mini` and `OPENAI_API_KEY=...`.
2. Local vLLM: set `OASIS_MODEL_PROVIDER=vllm`, the served model name,
   `OPENAI_BASE_URL`, and a non-empty placeholder `OPENAI_API_KEY`.

The tested local model is Qwen3.8-27B. Serve it with Qwen's `qwen3` reasoning
parser and `qwen3_coder` tool-call parser. The vLLM adapter in
`src/oasis/runtime.py` deliberately merges leading system messages because the
Qwen chat template accepts only one initial system message. Do not remove this
profile override without an end-to-end PydanticAI tool-call test.

Model inference is replaceable. Scientific tools, schemas, and deterministic
workflows must not depend on a provider-specific response format outside the
runtime adapter.

## Runtime layout

- `src/oasis/agent.py`: Agent instructions and toolset composition.
- `src/oasis/runtime.py`: provider selection and dependency wiring.
- `src/oasis/settings.py`: `.env` configuration.
- `src/oasis/toolsets/`: model-facing tools with typed results.
- `src/oasis/integrations/`: external services and Core Analyst adapters.
- `src/core_analyst/`: deterministic hazard and exposure workflows.
- `webgis/frontend/`: Leaflet frontend.
- `webgis/docker-compose.yml`: PostGIS and GeoServer services.

Default local ports are:

| Service | Port |
| --- | ---: |
| OASIS Agent API | 8000 |
| Local vLLM | 8001 |
| Frontend development server | 3000 |
| GeoServer | 8080 |
| PostGIS | 5432 |

Do not move vLLM to port 8000 while the OASIS API uses that port.

## Validation

Run focused tests while developing, then the full suite:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/pytest -p pytest_asyncio.plugin
```

Disabling global plugin autoload prevents unrelated system ROS pytest plugins
from breaking this repository's test collection. For model changes, verify all
of the following rather than relying only on a plain chat completion:

1. `curl http://127.0.0.1:8001/v1/models` succeeds in local mode.
2. The model emits a valid OpenAI-compatible function/tool call.
3. `oasis agent "Assess the available flood evidence for Glasgow"` completes.
4. `POST /agent/turn` returns a valid `MapAgentResponse` with tool events.
5. `GET /health` reports `semantic_model` as `configured`.

For data or analysis changes, also run `oasis data verify` and the relevant
static workflow. Never claim success based only on process startup.

## Code and safety rules

- Prefer simple, direct, readable research code and fail-fast behavior.
- Avoid speculative abstractions, broad exception handling, and redundant
  validation of trusted internal calls.
- Validate external inputs and surface actionable errors from files, networks,
  APIs, and model outputs.
- Keep provider-specific construction in the runtime/integration boundary and
  keep reusable tool results provider-neutral.
- Never commit credentials, `.env`, licensed source data, raw downloads, or
  generated rasters.
- Do not stop, replace, or delete user-owned processes, containers, caches, or
  outputs unless the user explicitly requests it.
- Do not publish public-facing warnings from rainfall or river-level
  observations alone. Preserve provenance, timestamps, scientific limitations,
  and human-review requirements.
