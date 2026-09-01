"""Runtime configuration without binding the core to a model provider."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field, SecretStr


class Settings(BaseModel):
    """Configuration injected into the agent and tools."""

    model: str = "test"
    model_provider: str = "auto"
    openai_base_url: str | None = None
    openai_api_key: SecretStr | None = None
    mimo_api_key: SecretStr | None = None
    mimo_base_url: str = "https://api.xiaomimimo.com/v1"
    http_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    metoffice_sample_grid_size: int = Field(default=5, ge=3, le=9)
    user_agent: str = "oasis-geoagent/0.1"
    default_area: str = "glasgow"
    geoserver_wms_url: str = (
        "http://127.0.0.1:8080/geoserver/glasgow_flood/wms"
    )
    geoserver_rest_url: str = "http://127.0.0.1:8080/geoserver/rest"
    geoserver_user: str = "admin"
    geoserver_password: SecretStr = SecretStr("geoserver")
    current_hazard_layer: str = "glasgow_flood:current_hazard_class_5m"
    current_hazard_raster_path: Path = Path(
        "webgis/.runtime/data_dir/data/glasgow_flood/"
        "current_hazard_class_5m/current_hazard_class_5m.geotiff"
    )
    current_hazard_output_dir: Path = Path("analysis/core-analyst/outputs/current")
    core_analyst_input_dir: Path = Path("analysis/core-analyst/Input")
    core_analyst_config_path: Path = Path(
        "analysis/core-analyst/config/pluvial_prediction_config.yaml"
    )
    core_analyst_config_dir: Path = Path("analysis/core-analyst/config")
    core_analyst_analysis_output_dir: Path = Path(
        "analysis/core-analyst/outputs/agent"
    )
    lcm2019_path: Path | None = None
    accept_data_licences: bool = False
    auto_prepare_data: bool = True
    nominatim_url: str = "https://nominatim.openstreetmap.org/search"
    osrm_url: str = "https://router.project-osrm.org"

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        return cls(
            model=os.getenv("OASIS_MODEL", "test"),
            model_provider=os.getenv("OASIS_MODEL_PROVIDER", "auto"),
            openai_base_url=os.getenv("OPENAI_BASE_URL"),
            openai_api_key=(
                SecretStr(value) if (value := os.getenv("OPENAI_API_KEY")) else None
            ),
            mimo_api_key=(
                SecretStr(value) if (value := os.getenv("MIMO_API_KEY")) else None
            ),
            mimo_base_url=os.getenv(
                "MIMO_BASE_URL", "https://api.xiaomimimo.com/v1"
            ),
            http_timeout_seconds=float(
                os.getenv("OASIS_HTTP_TIMEOUT_SECONDS", "30")
            ),
            metoffice_sample_grid_size=int(
                os.getenv("OASIS_METOFFICE_SAMPLE_GRID_SIZE", "5")
            ),
            user_agent=os.getenv("OASIS_USER_AGENT", "oasis-geoagent/0.1"),
            default_area=os.getenv("OASIS_DEFAULT_AREA", "glasgow"),
            geoserver_wms_url=os.getenv(
                "OASIS_GEOSERVER_WMS_URL",
                "http://127.0.0.1:8080/geoserver/glasgow_flood/wms",
            ),
            geoserver_rest_url=os.getenv(
                "OASIS_GEOSERVER_REST_URL", "http://127.0.0.1:8080/geoserver/rest"
            ),
            geoserver_user=os.getenv("OASIS_GEOSERVER_USER", "admin"),
            geoserver_password=SecretStr(
                os.getenv("OASIS_GEOSERVER_PASSWORD", "geoserver")
            ),
            current_hazard_layer=os.getenv(
                "OASIS_CURRENT_HAZARD_LAYER",
                "glasgow_flood:current_hazard_class_5m",
            ),
            current_hazard_raster_path=Path(
                os.getenv(
                    "OASIS_CURRENT_HAZARD_RASTER_PATH",
                    "webgis/.runtime/data_dir/data/glasgow_flood/"
                    "current_hazard_class_5m/current_hazard_class_5m.geotiff",
                )
            ),
            current_hazard_output_dir=Path(
                os.getenv(
                    "OASIS_CURRENT_HAZARD_OUTPUT_DIR",
                    "analysis/core-analyst/outputs/current",
                )
            ),
            core_analyst_input_dir=Path(
                os.getenv(
                    "OASIS_CORE_ANALYST_INPUT_DIR",
                    "analysis/core-analyst/Input",
                )
            ),
            core_analyst_config_path=Path(
                os.getenv(
                    "OASIS_CORE_ANALYST_CONFIG_PATH",
                    "analysis/core-analyst/config/pluvial_prediction_config.yaml",
                )
            ),
            core_analyst_config_dir=Path(
                os.getenv(
                    "OASIS_CORE_ANALYST_CONFIG_DIR",
                    "analysis/core-analyst/config",
                )
            ),
            core_analyst_analysis_output_dir=Path(
                os.getenv(
                    "OASIS_CORE_ANALYST_ANALYSIS_OUTPUT_DIR",
                    "analysis/core-analyst/outputs/agent",
                )
            ),
            lcm2019_path=(
                Path(value) if (value := os.getenv("OASIS_LCM2019_PATH")) else None
            ),
            accept_data_licences=_env_bool("OASIS_ACCEPT_DATA_LICENCES", False),
            auto_prepare_data=_env_bool("OASIS_AUTO_PREPARE_DATA", True),
            nominatim_url=os.getenv(
                "OASIS_NOMINATIM_URL",
                "https://nominatim.openstreetmap.org/search",
            ),
            osrm_url=os.getenv(
                "OASIS_OSRM_URL",
                "https://router.project-osrm.org",
            ),
        )

    @property
    def semantic_model_configured(self) -> bool:
        if self.model == "test":
            return False
        if self.model_provider == "mimo":
            return self.mimo_api_key is not None
        if self.model_provider == "vllm":
            return self.openai_base_url is not None and self.openai_api_key is not None
        if self.model.startswith("openai:"):
            return self.openai_api_key is not None
        return True


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
