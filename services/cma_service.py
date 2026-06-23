import json
from pathlib import Path
from typing import Any

import h5py
import numpy as np
import rasterio

from adapters import cma_adapter


DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "CMA"
NODATA = -999999.0


def get_display_data(variable: str | None = None, level_index: int = 0) -> dict[str, Any]:
    # 前端点击 CMA 类型时调用该函数，读取 CMA 目录下的 meta.json 和 PNG。
    _ensure_latest_meta()
    meta_files = _meta_files()
    png_files = sorted(DATA_DIR.rglob("*.png"), key=lambda item: item.stat().st_mtime, reverse=True)

    meta_json = None
    if meta_files:
        with meta_files[0].open("r", encoding="utf-8") as file:
            meta_json = json.load(file)

    variables = _display_variables(meta_json)
    grid = None
    if meta_json:
        try:
            grid = get_grid_data(variable=variable, level_index=level_index)
        except ValueError:
            grid = None

    return {
        "business_type": "CMA",
        "meta_file": str(meta_files[0]).replace("\\", "/") if meta_files else None,
        "meta_json": meta_json,
        "png": str(png_files[0]).replace("\\", "/") if png_files else None,
        "png_files": [str(path).replace("\\", "/") for path in png_files],
        "variables": variables,
        "grid": grid,
    }


def get_grid_data(variable: str | None = None, level_index: int = 0) -> dict[str, Any]:
    meta = _latest_meta()
    source_file = _source_file(meta)
    file_format = str(meta.get("file_format") or source_file.suffix.lstrip(".")).upper()
    variable_name = variable or _primary_variable(meta)

    if file_format == "NC" or source_file.suffix.lower() == ".nc":
        payload = _read_nc_grid(source_file, meta, variable_name, level_index)
    elif source_file.suffix.lower() in {".grib", ".grib2"}:
        payload = _read_grib_grid(source_file, meta, variable_name)
    else:
        raise ValueError(f"Unsupported CMA grid file: {source_file.name}")

    return {
        "business_type": "CMA",
        "dataset_id": meta.get("dataset_id"),
        "file": source_file.name,
        "variable": payload["variable"],
        "label": payload["label"],
        "unit": _clean_unit(payload["unit"]),
        "level_index": payload.get("level_index", 0),
        "width": payload["width"],
        "height": payload["height"],
        "extent": payload["extent"],
        "min": payload["min"],
        "max": payload["max"],
        "mean": payload["mean"],
        "nodata": NODATA,
        "values": payload["values"],
        "variables": _display_variables(meta),
        "meta": _grid_meta(meta, payload),
    }


def _latest_meta() -> dict[str, Any]:
    _ensure_latest_meta()
    meta_files = _meta_files()
    if not meta_files:
        raise ValueError("No CMA meta.json found. Parse a CMA file first.")
    with meta_files[0].open("r", encoding="utf-8") as file:
        return json.load(file)


def _source_file(meta: dict[str, Any]) -> Path:
    by_name = DATA_DIR / str(meta.get("file", ""))
    if by_name.exists():
        return by_name

    source = Path(str(meta.get("source_file", "")))
    if source.exists():
        return source

    candidates = sorted(
        _source_files(),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise ValueError("No CMA source data file found.")
    return candidates[0]


def _meta_files() -> list[Path]:
    files = sorted(DATA_DIR.rglob("*.meta.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    fallback = DATA_DIR / "meta.json"
    if fallback.exists() and fallback not in files:
        files.append(fallback)
    return files


def _source_files() -> list[Path]:
    return [
        path
        for pattern in ("*.nc", "*.grib", "*.grib2")
        for path in DATA_DIR.rglob(pattern)
        if path.is_file()
    ]


def _ensure_latest_meta() -> None:
    sources = sorted(_source_files(), key=lambda item: item.stat().st_mtime, reverse=True)
    if not sources:
        return

    latest_source = sources[0]
    expected_meta = latest_source.with_name(f"{latest_source.name}.meta.json")
    if expected_meta.exists() and expected_meta.stat().st_mtime >= latest_source.stat().st_mtime:
        return

    cma_adapter.process_file(str(latest_source), data_type="CMA")


def _primary_variable(meta: dict[str, Any]) -> str:
    cma = meta.get("extra", {}).get("cma", {})
    primary = cma.get("primary_variable") or meta.get("variables", [None])[0]
    if not primary:
        raise ValueError("No CMA variable found in meta.json.")
    return str(primary)


def _display_variables(meta: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not meta:
        return []
    cma = meta.get("extra", {}).get("cma", {})
    products = cma.get("products", {})
    for product in products.values():
        variables = product.get("variables", [])
        if variables:
            return [
                {
                    "name": item.get("name"),
                    "label": item.get("long_name") or item.get("name"),
                    "unit": item.get("unit", ""),
                    "dims": item.get("dims", []),
                    "shape": item.get("shape", []),
                }
                for item in variables
                if _is_grid_variable(item)
            ]
    return [{"name": name, "label": name, "unit": ""} for name in meta.get("variables", [])]


def _is_grid_variable(item: dict[str, Any]) -> bool:
    dims = item.get("dims") or []
    shape = item.get("shape") or []
    return bool(item.get("band")) or dims[-2:] == ["lat", "lon"] or len(shape) == 2


def _read_nc_grid(source_file: Path, meta: dict[str, Any], variable: str, level_index: int) -> dict[str, Any]:
    with h5py.File(source_file, "r") as dataset:
        if variable not in dataset:
            variable = _first_available_nc_variable(dataset)
        item = dataset[variable]
        attrs = {key: _decode_attr(value) for key, value in item.attrs.items()}
        raw = item[:]
        if raw.ndim == 3:
            safe_level = min(max(level_index, 0), raw.shape[0] - 1)
            raw = raw[safe_level, :, :]
        elif raw.ndim == 2:
            safe_level = 0
        else:
            raise ValueError(f"CMA variable {variable} is not a 2D grid.")
        data = _clean_grid(raw, attrs.get("_FillValue") or attrs.get("missing_value"))
        extent = _nc_extent(dataset, meta)

    return _grid_payload(
        variable=variable,
        label=str(attrs.get("long_name") or variable),
        unit=_clean_unit(attrs.get("units") or ""),
        data=data,
        extent=extent,
        level_index=safe_level,
    )


def _first_available_nc_variable(dataset: h5py.File) -> str:
    for name, item in dataset.items():
        if isinstance(item, h5py.Dataset) and name not in {"lat", "lon"} and len(item.shape) in {2, 3}:
            return name
    raise ValueError("No renderable CMA grid variable found.")


def _read_grib_grid(source_file: Path, meta: dict[str, Any], variable: str) -> dict[str, Any]:
    with rasterio.open(source_file) as dataset:
        band_index = 1
        tags = dataset.tags(1)
        for band in range(1, dataset.count + 1):
            band_tags = dataset.tags(band)
            if band_tags.get("GRIB_ELEMENT") == variable:
                band_index = band
                tags = band_tags
                break
        data = _clean_grid(dataset.read(band_index), dataset.nodata)
        extent = [float(dataset.bounds.left), float(dataset.bounds.bottom), float(dataset.bounds.right), float(dataset.bounds.top)]
    return _grid_payload(
        variable=tags.get("GRIB_ELEMENT", variable),
        label=tags.get("GRIB_COMMENT") or tags.get("GRIB_ELEMENT") or variable,
        unit=_clean_unit(tags.get("GRIB_UNIT", "")),
        data=data,
        extent=extent or meta.get("extent"),
        level_index=0,
    )


def _nc_extent(dataset: h5py.File, meta: dict[str, Any]) -> list[float]:
    if "lon" in dataset and "lat" in dataset:
        lon = np.array(dataset["lon"][:], dtype="float64")
        lat = np.array(dataset["lat"][:], dtype="float64")
        return [float(np.nanmin(lon)), float(np.nanmin(lat)), float(np.nanmax(lon)), float(np.nanmax(lat))]
    return list(meta.get("extent") or meta.get("bbox") or [73, 15, 135, 55])


def _decode_attr(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, np.bytes_):
        return bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, np.ndarray) and value.size == 1:
        return _decode_attr(value.reshape(-1)[0])
    return value


def _clean_unit(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text.startswith("[") and text.endswith("]"):
        return text[1:-1].strip()
    return text


def _clean_grid(array: np.ndarray, missing: Any) -> np.ndarray:
    data = np.array(array, dtype="float32")
    try:
        missing_value = float(np.array(missing).reshape(-1)[0])
        data[data == missing_value] = np.nan
    except Exception:
        pass
    data[np.isinf(data)] = np.nan
    return data


def _grid_payload(variable: str, label: str, unit: str, data: np.ndarray, extent: list[float], level_index: int) -> dict[str, Any]:
    finite = data[np.isfinite(data)]
    min_value = float(np.nanmin(finite)) if finite.size else 0.0
    max_value = float(np.nanmax(finite)) if finite.size else 1.0
    mean_value = float(np.nanmean(finite)) if finite.size else 0.0
    values = np.where(np.isfinite(data), data, NODATA).astype("float32")
    return {
        "variable": variable,
        "label": label,
        "unit": unit,
        "level_index": level_index,
        "width": int(values.shape[1]),
        "height": int(values.shape[0]),
        "extent": [float(item) for item in extent],
        "min": round(min_value, 6),
        "max": round(max_value, 6),
        "mean": round(mean_value, 6),
        "values": values.reshape(-1).round(6).tolist(),
    }


def _grid_meta(meta: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    variable_names = [item.get("name") for item in _display_variables(meta) if item.get("name")]
    return {
        **{key: value for key, value in meta.items() if key in {"file", "time", "range", "grid", "missing", "vars", "steps"}},
        "element": ", ".join(variable_names) or str(payload["variable"]),
        "unit": _clean_unit(payload["unit"]),
        "extent": payload["extent"],
        "grid": f"{payload['width']} x {payload['height']}",
        "min": payload["min"],
        "max": payload["max"],
        "mean": payload["mean"],
    }
