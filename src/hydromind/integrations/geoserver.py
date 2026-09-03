"""Publish analysis rasters as GeoServer WMS layers."""

from __future__ import annotations

from pathlib import Path
import re

import httpx

from hydromind.models.analysis import AnalysisMapLayer


class GeoServerPublisher:
    def __init__(
        self,
        rest_url: str,
        wms_url: str,
        username: str,
        password: str,
        workspace: str = "glasgow_flood",
    ) -> None:
        self.rest_url = rest_url.rstrip("/")
        self.wms_url = wms_url
        self.auth = (username, password)
        self.workspace = workspace

    def publish_raster(self, path: str | Path, *, name: str, label: str) -> AnalysisMapLayer:
        raster = Path(path)
        layer = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")
        url = (
            f"{self.rest_url}/workspaces/{self.workspace}/coveragestores/"
            f"{layer}/file.geotiff?configure=first&coverageName={layer}"
        )
        with raster.open("rb") as source, httpx.Client(auth=self.auth, timeout=180) as client:
            response = client.put(url, content=source, headers={"Content-Type": "image/tiff"})
            response.raise_for_status()
            if "class" in name:
                style = (
                    f"<layer><defaultStyle><name>hazard_class</name>"
                    f"<workspace>{self.workspace}</workspace></defaultStyle></layer>"
                )
                response = client.put(
                    f"{self.rest_url}/layers/{self.workspace}%3A{layer}",
                    content=style,
                    headers={"Content-Type": "application/xml"},
                )
                response.raise_for_status()
        return AnalysisMapLayer(
            id=f"wms-{layer}",
            label=label,
            kind="wms",
            url=self.wms_url,
            layer_name=f"{self.workspace}:{layer}",
            style="hazard_class" if "class" in name else "",
        )

    def layer_exists(self, layer_name: str) -> bool:
        """Check that a layer associated with a published local artifact exists."""

        encoded = layer_name.replace(":", "%3A")
        with httpx.Client(auth=self.auth, timeout=20) as client:
            response = client.get(f"{self.rest_url}/layers/{encoded}.json")
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return True
