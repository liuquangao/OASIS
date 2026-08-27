from __future__ import annotations

import os
from pathlib import Path


ALIASES = {
    "METOFFICE_ATMOSPHERIC_API_KEY": "METOFFICE_API_KEY",
    "METOFFICE_SITE_SPECIFIC_API_KEY": "METOFFICE_SITE_API_KEY",
    "METOFFICE_MAPIMAGE_API_KEY": "METOFFICE_MAP_API_KEY",
    "METOFFICE_WARNINGS_API_KEY": "METOFFICE_NSWWWS_API_KEY",
    "METOFFICE_WARNINGS_API_KEY": "METOFFICE_NSWWWS_API_KEY",
}


def load_env_file(path: str | Path = "env.env", override: bool = False) -> None:
    path = Path(path)
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = value

    for source, target in ALIASES.items():
        if source in os.environ and (override or target not in os.environ):
            os.environ[target] = os.environ[source]
