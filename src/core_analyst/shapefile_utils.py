from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union


@dataclass(frozen=True)
class ShapefileMetadata:
    path: str
    shape_type: int
    record_count: int
    fields: list[dict[str, Any]]
    crs: str | None
    bbox: tuple[float, float, float, float]


@dataclass(frozen=True)
class ShapefileFeature:
    geometry: Any
    properties: dict[str, Any]
    bbox: tuple[float, float, float, float]


BNG_PRJ_HINTS = ("British_National_Grid", "OSGB_1936", "Airy_1830")


def shapefile_metadata(path: str | Path) -> ShapefileMetadata:
    path = Path(path)
    with path.open("rb") as handle:
        header = handle.read(100)
    if len(header) < 100:
        raise ValueError(f"{path} is not a valid shapefile header.")
    shape_type = struct.unpack("<i", header[32:36])[0]
    bbox = struct.unpack("<4d", header[36:68])
    fields, record_count = read_dbf_header(path.with_suffix(".dbf"))
    return ShapefileMetadata(
        path=str(path),
        shape_type=shape_type,
        record_count=record_count,
        fields=fields,
        crs=read_prj_crs(path.with_suffix(".prj")),
        bbox=(bbox[0], bbox[1], bbox[2], bbox[3]),
    )


def iter_shapefile_features(path: str | Path) -> Iterator[ShapefileFeature]:
    path = Path(path)
    records = read_dbf_records(path.with_suffix(".dbf"))
    with path.open("rb") as handle:
        handle.seek(100)
        index = 0
        while True:
            record_header = handle.read(8)
            if not record_header:
                break
            if len(record_header) != 8:
                raise ValueError(f"Malformed shapefile record header in {path}.")
            _, content_length_words = struct.unpack(">2i", record_header)
            content = handle.read(content_length_words * 2)
            geometry, bbox = _geometry_from_record(content)
            if geometry is not None:
                properties = records[index] if index < len(records) else {}
                yield ShapefileFeature(geometry=geometry, properties=properties, bbox=bbox)
            index += 1


def read_prj_crs(path: str | Path) -> str | None:
    path = Path(path)
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="ignore")
    if all(hint in text for hint in BNG_PRJ_HINTS[:2]):
        return "EPSG:27700"
    return text.strip() or None


def read_dbf_header(path: str | Path) -> tuple[list[dict[str, Any]], int]:
    path = Path(path)
    with path.open("rb") as handle:
        header = handle.read(32)
        if len(header) != 32:
            raise ValueError(f"{path} is not a valid DBF header.")
        record_count = struct.unpack("<I", header[4:8])[0]
        header_length = struct.unpack("<H", header[8:10])[0]
        fields = []
        while handle.tell() < header_length:
            descriptor = handle.read(32)
            if not descriptor or descriptor[0] == 0x0D:
                break
            name = descriptor[:11].split(b"\x00", 1)[0].decode("ascii", errors="ignore")
            fields.append(
                {
                    "name": name,
                    "type": chr(descriptor[11]),
                    "length": descriptor[16],
                    "decimal_count": descriptor[17],
                }
            )
    return fields, record_count


def read_dbf_records(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    fields, record_count = read_dbf_header(path)
    with path.open("rb") as handle:
        header = handle.read(32)
        header_length = struct.unpack("<H", header[8:10])[0]
        record_length = struct.unpack("<H", header[10:12])[0]
        handle.seek(header_length)
        records = []
        for _ in range(record_count):
            raw = handle.read(record_length)
            if len(raw) != record_length:
                break
            if raw[:1] == b"*":
                continue
            offset = 1
            record: dict[str, Any] = {}
            for field in fields:
                length = int(field["length"])
                text = raw[offset:offset + length].decode("utf-8", errors="ignore").strip()
                record[field["name"]] = _parse_dbf_value(text, field)
                offset += length
            records.append(record)
    return records


def _parse_dbf_value(text: str, field: dict[str, Any]) -> Any:
    if text == "":
        return None
    if field["type"] in {"N", "F"}:
        try:
            if int(field.get("decimal_count") or 0) == 0:
                return int(text)
            return float(text)
        except ValueError:
            return text
    return text


def _geometry_from_record(content: bytes) -> tuple[Any | None, tuple[float, float, float, float]]:
    if len(content) < 4:
        return None, (0.0, 0.0, 0.0, 0.0)
    shape_type = struct.unpack("<i", content[:4])[0]
    if shape_type == 0:
        return None, (0.0, 0.0, 0.0, 0.0)
    if shape_type in {5, 15, 25, 31}:
        return _polygon_from_record(content)
    raise ValueError(f"Unsupported shapefile shape type {shape_type}.")


def _polygon_from_record(content: bytes) -> tuple[Any | None, tuple[float, float, float, float]]:
    if len(content) < 44:
        return None, (0.0, 0.0, 0.0, 0.0)
    xmin, ymin, xmax, ymax = struct.unpack("<4d", content[4:36])
    num_parts, num_points = struct.unpack("<2i", content[36:44])
    parts_offset = 44
    points_offset = parts_offset + (num_parts * 4)
    parts = list(struct.unpack(f"<{num_parts}i", content[parts_offset:points_offset]))
    point_bytes = content[points_offset:points_offset + (num_points * 16)]
    points = list(struct.unpack(f"<{num_points * 2}d", point_bytes))
    xy = [(points[i], points[i + 1]) for i in range(0, len(points), 2)]
    part_starts = parts + [num_points]
    polygons = []
    for start, end in zip(part_starts, part_starts[1:]):
        ring = xy[start:end]
        if len(ring) < 4:
            continue
        polygon = Polygon(ring)
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        if not polygon.is_empty:
            polygons.append(polygon)
    if not polygons:
        return None, (xmin, ymin, xmax, ymax)
    if len(polygons) == 1:
        return polygons[0], (xmin, ymin, xmax, ymax)
    geometry = unary_union(polygons)
    if isinstance(geometry, Polygon | MultiPolygon):
        return geometry, (xmin, ymin, xmax, ymax)
    return geometry.buffer(0), (xmin, ymin, xmax, ymax)
