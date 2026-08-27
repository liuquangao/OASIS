"""Plan transferable risk analyses from the component and data registries."""

from __future__ import annotations

from typing import Any


HAZARD_REQUIREMENTS = {
    "pluvial": ["dem", "land_cover", "built_up", "greenspace", "river_network", "rainfall"],
    "fluvial": ["dem", "river_network", "fluvial_reference", "river_level"],
    "coastal": ["dem", "coastal_reference", "tide_sea_level"],
}

SOURCE_CATALOG = {
    "dem": ("Copernicus DEM", "https://dataspace.copernicus.eu/", "raster"),
    "land_cover": ("Copernicus Land Monitoring Service", "https://land.copernicus.eu/", "raster"),
    "built_up": ("OpenStreetMap", "https://www.openstreetmap.org/", "vector"),
    "greenspace": ("OpenStreetMap", "https://www.openstreetmap.org/", "vector"),
    "river_network": ("OpenStreetMap", "https://www.openstreetmap.org/", "vector"),
    "rainfall": ("Met Office DataHub", "https://datahub.metoffice.gov.uk/", "time_series"),
    "river_level": ("National hydrometric authority", "", "time_series"),
    "tide_sea_level": ("National Tidal and Sea Level Facility", "https://ntslf.org/", "time_series"),
}


def plan_generalized_analysis(
    *,
    area: str,
    hazard_type: str,
    temporal_scope: str,
    registry: list[dict[str, Any]],
) -> dict[str, Any]:
    required = HAZARD_REQUIREMENTS.get(hazard_type, ["dem", f"{hazard_type}_evidence"])
    available = {
        str(item["dataset"])
        for item in registry
        if item.get("status") == "available"
    }
    missing = [name for name in required if name not in available]
    discovered = [
        {"dataset": name, "provider": SOURCE_CATALOG.get(name, ("Authoritative local provider", "", "unknown"))[0],
         "url": SOURCE_CATALOG.get(name, ("", "", ""))[1],
         "adapter_type": SOURCE_CATALOG.get(name, ("", "", "unknown"))[2],
         "status": "local" if name in available else "candidate"}
        for name in required
    ]
    glasgow_flood = area.strip().lower() == "glasgow" and hazard_type in HAZARD_REQUIREMENTS
    return {
        "area": area,
        "hazard_type": hazard_type,
        "temporal_scope": temporal_scope,
        "executable_now": glasgow_flood and not missing,
        "reusable_components": [
            "data registry",
            "raster alignment and validation",
            "weighted overlay and classification",
            "exposure, vulnerability, and priority analysts",
            "scenario comparison, provenance, GeoServer publication",
        ],
        "required_datasets": required,
        "missing_datasets": missing,
        "extension_points": [
            f"register authoritative {area} source adapters",
            f"add {hazard_type} factor configuration",
            "validate thresholds against observed outcomes",
        ],
        "workflow": [
            "discover and register data",
            "prepare a common study-area grid",
            "run hazard factors",
            "calculate exposure and vulnerability",
            "rank priorities with explicit weights",
            "publish spatial outputs and retain provenance",
        ],
        "discovered_sources": discovered,
    }
