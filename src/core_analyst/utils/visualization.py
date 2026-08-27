from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import rasterio


def create_debug_visualization(raster_paths: dict[str, str | Path], output_path: str | Path) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    titles = list(raster_paths)
    cols = 3
    rows = (len(titles) + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(12, 4 * rows))
    axes_list = axes.ravel() if hasattr(axes, "ravel") else [axes]

    for ax, title in zip(axes_list, titles):
        with rasterio.open(raster_paths[title]) as dataset:
            data = dataset.read(1)
        cmap = "viridis" if "class" not in title else "RdYlBu_r"
        image = ax.imshow(data, cmap=cmap)
        ax.set_title(title)
        ax.axis("off")
        fig.colorbar(image, ax=ax, fraction=0.046, pad=0.04)

    for ax in axes_list[len(titles):]:
        ax.axis("off")

    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
