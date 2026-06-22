from datetime import datetime
from pathlib import Path
import traceback

import numpy as np
import xarray as xr
import cfgrib

from adapters.base import process_basic_file


def _now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_float(x, digits=3):
    try:
        x = float(x)
        if np.isnan(x) or np.isinf(x):
            return None
        return round(x, digits)
    except Exception:
        return None


def _open_grib_groups(file_path: str):
    """
    读取 GRIB/GRIB2 文件。

    用 cfgrib.open_datasets 的原因：
    一个 GRIB 文件里经常有多个 group，比如：
    - 不同 level
    - 不同 stepType
    - instant / accum
    - surface / heightAboveGround

    open_datasets 比 xr.open_dataset 更稳。
    """
    try:
        groups = cfgrib.open_datasets(
            file_path,
            backend_kwargs={"indexpath": ""}
        )
        groups = [ds for ds in groups if len(ds.data_vars) > 0]

        if len(groups) == 0:
            raise RuntimeError("cfgrib.open_datasets 没有读取到有效变量")

        return groups

    except Exception:
        ds = xr.open_dataset(
            file_path,
            engine="cfgrib",
            backend_kwargs={"indexpath": ""}
        )
        return [ds]


def _find_lat_lon_names(ds):
    lat_candidates = ["latitude", "lat"]
    lon_candidates = ["longitude", "lon"]

    lat_name = None
    lon_name = None

    for name in lat_candidates:
        if name in ds.coords:
            lat_name = name
            break

    for name in lon_candidates:
        if name in ds.coords:
            lon_name = name
            break

    return lat_name, lon_name


def _format_time_value(x):
    try:
        return str(np.datetime_as_string(x, unit="m"))
    except Exception:
        return str(x)


def _summarize_coord(ds, names):
    for name in names:
        if name in ds.coords:
            values = np.atleast_1d(ds[name].values)

            if len(values) == 0:
                return "待解析"

            if len(values) == 1:
                return _format_time_value(values[0])

            return f"{_format_time_value(values[0])} 至 {_format_time_value(values[-1])}"

    return "待解析"


def _summarize_steps(ds):
    if "step" not in ds.coords:
        return "待解析"

    values = np.atleast_1d(ds["step"].values)

    if len(values) == 0:
        return "待解析"

    if len(values) <= 8:
        return ", ".join([str(v) for v in values])

    return f"{values[0]} 至 {values[-1]}，共 {len(values)} 个时效"


def _get_resolution(lat, lon):
    try:
        lat = np.asarray(lat, dtype=float)
        lon = np.asarray(lon, dtype=float)

        if len(lat) < 2 or len(lon) < 2:
            return "待解析"

        dlat = np.nanmedian(np.abs(np.diff(lat)))
        dlon = np.nanmedian(np.abs(np.diff(lon)))

        return f"{_safe_float(dlat, 4)}° × {_safe_float(dlon, 4)}°"
    except Exception:
        return "待解析"


def _infer_var_type(var_name: str, units: str, long_name: str):
    n = (var_name or "").lower()
    u = (units or "").lower()
    l = (long_name or "").lower()

    if n in ["t2m", "d2m", "2t", "2d", "tmp", "dpt"] or "temperature" in l or "dewpoint" in l:
        return "temperature"

    if n in ["tp", "cp", "lsp", "apcp"] or "precipitation" in l or "rain" in l:
        return "precipitation"

    if n in ["sp", "msl", "mslp", "prmsl"] or "pressure" in l:
        return "pressure"

    if n in ["u10", "v10", "u", "v", "10u", "10v", "ugrd", "vgrd"] or "wind" in l or "m s**-1" in u:
        return "wind"

    return "generic"


def _convert_values(var_name: str, units: str, long_name: str, values):
    """
    转成前端更容易理解的单位。
    """
    values = np.asarray(values, dtype=float)
    var_type = _infer_var_type(var_name, units, long_name)

    if units == "K":
        return values - 273.15, "°C", "K → °C", var_type

    if units == "Pa":
        return values / 100.0, "hPa", "Pa → hPa", var_type

    if var_type == "precipitation" and units in ["m", "metre", "meter"]:
        return values * 1000.0, "mm", "m → mm", var_type

    # GFS APCP 常见单位 kg m**-2，数值上可近似理解为 mm 水深
    if var_type == "precipitation" and units in ["kg m**-2", "kg m-2"]:
        return values, "mm", "kg m**-2 → mm", var_type

    return values, units or "未知", "no_conversion", var_type


def _choose_main_variable(groups):
    """
    选择一个主要变量用于统计展示。
    优先选温度、降水、气压等前端更容易展示的变量。
    """
    preferred = [
        "t2m", "2t", "tmp",
        "tp", "apcp",
        "prmsl", "msl", "sp",
        "d2m", "2d", "dpt",
        "u10", "v10", "ugrd", "vgrd"
    ]

    candidates = []

    for gi, ds in enumerate(groups):
        lat_name, lon_name = _find_lat_lon_names(ds)

        if lat_name is None or lon_name is None:
            continue

        for var_name in ds.data_vars:
            da = ds[var_name]

            if lat_name in da.dims and lon_name in da.dims:
                candidates.append((gi, ds, var_name))

    if len(candidates) == 0:
        return None, None, None

    lower_map = {v.lower(): (gi, ds, v) for gi, ds, v in candidates}

    for p in preferred:
        if p.lower() in lower_map:
            return lower_map[p.lower()]

    return candidates[0]


def _to_2d_or_3d_array(ds, var_name):
    """
    把变量压成：
    - 二维：lat × lon
    - 三维：time/step × lat × lon

    其他维度先取第 0 层。
    """
    da = ds[var_name]

    lat_name, lon_name = _find_lat_lon_names(ds)

    if lat_name is None or lon_name is None:
        raise ValueError("找不到 latitude/longitude 坐标")

    time_dim = None
    for d in ["time", "valid_time", "step"]:
        if d in da.dims:
            time_dim = d
            break

    for dim in list(da.dims):
        if dim in [lat_name, lon_name, time_dim]:
            continue
        da = da.isel({dim: 0})

    if time_dim is not None:
        da = da.transpose(time_dim, lat_name, lon_name)
    else:
        da = da.transpose(lat_name, lon_name)

    arr = np.asarray(da.values, dtype=float)

    lat = np.asarray(ds[lat_name].values, dtype=float)
    lon = np.asarray(ds[lon_name].values, dtype=float)

    # 统一成：纬度北到南，经度西到东
    if len(lat) > 1 and lat[0] < lat[-1]:
        lat = lat[::-1]
        if arr.ndim == 3:
            arr = arr[:, ::-1, :]
        else:
            arr = arr[::-1, :]

    if len(lon) > 1 and lon[0] > lon[-1]:
        lon = lon[::-1]
        if arr.ndim == 3:
            arr = arr[:, :, ::-1]
        else:
            arr = arr[:, ::-1]

    return arr, lat, lon


def _stats(values):
    arr = np.asarray(values, dtype=float)
    valid = arr[np.isfinite(arr)]

    if valid.size == 0:
        return {
            "max": "待解析",
            "min": "待解析",
            "mean": "待解析",
            "valid_count": 0,
            "total_count": int(arr.size),
            "missing_count": int(arr.size),
            "missing_ratio": 1.0
        }

    total = int(arr.size)
    valid_count = int(valid.size)
    missing_count = total - valid_count

    return {
        "max": _safe_float(np.nanmax(valid), 3),
        "min": _safe_float(np.nanmin(valid), 3),
        "mean": _safe_float(np.nanmean(valid), 3),
        "valid_count": valid_count,
        "total_count": total,
        "missing_count": missing_count,
        "missing_ratio": missing_count / total if total > 0 else 0
    }


def _make_bars(values, bins=5):
    arr = np.asarray(values, dtype=float)
    valid = arr[np.isfinite(arr)]

    if valid.size == 0:
        return [0] * bins

    try:
        counts, _ = np.histogram(valid, bins=bins)
        max_count = np.max(counts)

        if max_count <= 0:
            return [0] * bins

        # 转成 0-100 的柱状图高度
        bars = [int(round(c / max_count * 100)) for c in counts]
        return bars

    except Exception:
        return [0] * bins


def _make_trend(values, target_len=8):
    arr = np.asarray(values, dtype=float)

    if arr.ndim == 3:
        # time × lat × lon，每个时效求区域平均
        series = np.nanmean(arr, axis=(1, 2))
    elif arr.ndim == 2:
        # 没有时间维度，就重复一个平均值
        mean_value = np.nanmean(arr)
        series = np.array([mean_value] * target_len)
    else:
        series = np.ravel(arr)

    series = np.asarray(series, dtype=float)
    series = series[np.isfinite(series)]

    if series.size == 0:
        return [0] * target_len

    if series.size == target_len:
        return [_safe_float(v, 3) for v in series]

    # 重采样成固定 8 个点
    old_x = np.linspace(0, 1, len(series))
    new_x = np.linspace(0, 1, target_len)
    sampled = np.interp(new_x, old_x, series)

    return [_safe_float(v, 3) for v in sampled]


def _quality_text(missing_ratio):
    if missing_ratio is None:
        return "待检查"

    if missing_ratio == 0:
        return "正常"

    if missing_ratio < 0.05:
        return "基本正常，少量缺测"

    if missing_ratio < 0.2:
        return "存在一定缺测"

    return "缺测较多"


def _alert_text(var_type, max_value):
    if max_value in ["待解析", None]:
        return "无"

    try:
        x = float(max_value)
    except Exception:
        return "无"

    if var_type == "temperature" and x >= 35:
        return "高温风险"

    if var_type == "precipitation" and x >= 50:
        return "强降水风险"

    if var_type == "wind" and x >= 17:
        return "大风风险"

    return "无"


def _collect_variable_names(groups):
    names = []

    for ds in groups:
        for v in ds.data_vars:
            short_name = str(ds[v].attrs.get("GRIB_shortName", v))
            long_name = str(ds[v].attrs.get("long_name", ""))
            units = str(ds[v].attrs.get("units", ""))

            if long_name:
                names.append(f"{v}({long_name}, {units})")
            else:
                names.append(f"{v}({short_name}, {units})")

    if len(names) == 0:
        return "待解析"

    text = "; ".join(names)

    # 防止前端卡片太长
    if len(text) > 500:
        text = text[:500] + "..."

    return text


def process_file(file_path: str, data_type: str = "GFS") -> dict:
    """
    GFS 文件处理入口。

    这个函数是给项目统一调用的。
    不要改函数名，不要改参数名。
    """
    weather_info = {
        "source": "GFS",
        "product": "GFS 数值预报产品",
        "element": "GRIB2 变量",
        "time": "待解析",
        "level": "待解析",
        "range": "待解析",
        "resolution": "待解析",
        "grid": "待解析",
        "validGrid": "待解析",
        "coverage": "待解析",
        "missing": "待解析",
        "unit": "待解析",
        "variables": "待解析",
        "steps": "待解析",
        "status": "已接收",
        "quality": "待解析",
        "max": "待解析",
        "min": "待解析",
        "mean": "待解析",
        "alert": "无",
        "update": _now_str(),
        "bars": [0, 0, 0, 0, 0],
        "trend": [0, 0, 0, 0, 0, 0, 0, 0],
    }

    try:
        path = Path(file_path)

        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        groups = _open_grib_groups(str(path))

        group_index, ds, var_name = _choose_main_variable(groups)

        if ds is None or var_name is None:
            raise RuntimeError("没有找到可用于地图展示的二维经纬度变量")

        da = ds[var_name]

        units = str(da.attrs.get("units", ""))
        long_name = str(da.attrs.get("long_name", var_name))
        short_name = str(da.attrs.get("GRIB_shortName", var_name))
        level_type = str(da.attrs.get("GRIB_typeOfLevel", "待解析"))
        step_type = str(da.attrs.get("GRIB_stepType", "待解析"))

        raw_arr, lat, lon = _to_2d_or_3d_array(ds, var_name)
        values, display_unit, conversion, var_type = _convert_values(
            var_name, units, long_name, raw_arr
        )

        stat = _stats(values)

        lat_min = _safe_float(np.nanmin(lat), 4)
        lat_max = _safe_float(np.nanmax(lat), 4)
        lon_min = _safe_float(np.nanmin(lon), 4)
        lon_max = _safe_float(np.nanmax(lon), 4)

        grid_text = f"{len(lat)} × {len(lon)}"
        range_text = f"纬度 {lat_min} ~ {lat_max}，经度 {lon_min} ~ {lon_max}"
        valid_grid_text = f"{stat['valid_count']} / {stat['total_count']}"
        coverage_text = f"{(1 - stat['missing_ratio']) * 100:.2f}%"
        missing_text = f"{stat['missing_ratio'] * 100:.2f}%"

        weather_info.update({
            "source": data_type,
            "product": "GFS 数值预报产品",
            "element": f"{var_name} / {long_name}",
            "time": _summarize_coord(ds, ["valid_time", "time"]),
            "level": f"{level_type}，stepType={step_type}",
            "range": range_text,
            "resolution": _get_resolution(lat, lon),
            "grid": grid_text,
            "validGrid": valid_grid_text,
            "coverage": coverage_text,
            "missing": missing_text,
            "unit": display_unit,
            "variables": _collect_variable_names(groups),
            "steps": _summarize_steps(ds),
            "status": "解析成功",
            "quality": _quality_text(stat["missing_ratio"]),
            "max": stat["max"],
            "min": stat["min"],
            "mean": stat["mean"],
            "alert": _alert_text(var_type, stat["max"]),
            "update": _now_str(),
            "bars": _make_bars(values, bins=5),
            "trend": _make_trend(values, target_len=8),

            # 下面这些是额外字段，前端如果不用也没关系
            "mainVariable": var_name,
            "mainVariableName": long_name,
            "shortName": short_name,
            "rawUnit": units,
            "displayUnit": display_unit,
            "conversion": conversion,
            "varType": var_type,
            "groupIndex": group_index,
            "latMin": lat_min,
            "latMax": lat_max,
            "lonMin": lon_min,
            "lonMax": lon_max,
            "fileSizeMB": round(path.stat().st_size / 1024 / 1024, 3),
        })

    except Exception as e:
        weather_info.update({
            "status": "解析失败",
            "quality": "异常",
            "alert": f"解析失败: {str(e)}",
            "update": _now_str(),
            "error": str(e),
            "traceback": traceback.format_exc()
        })

    file_format = "GRIB2" if str(file_path).lower().endswith(".grib2") else "GRIB"

    basic_result = process_basic_file(
        file_path,
        data_type=data_type,
        file_format=file_format,
        weather_info=weather_info
    )

    if isinstance(basic_result, dict):
        # 1. 保留原始统一结构
        basic_result["weather_info"] = weather_info

        # 2. 同时把 weather_info 合并到最外层，方便前端直接取 status/source/product 等字段
        basic_result.update(weather_info)

        # 3. 补强 process_basic_file 外层字段，避免 variables/times/levels/bbox 为空
        variable_text = weather_info.get("variables", "")
        variable_items = []
        if isinstance(variable_text, str) and variable_text not in ["", "待解析"]:
            variable_items = [x.strip() for x in variable_text.split(";") if x.strip()]

        time_text = weather_info.get("time", "")
        times = []
        if isinstance(time_text, str) and time_text not in ["", "待解析"]:
            times = [time_text]

        level_text = weather_info.get("level", "")
        levels = []
        if isinstance(level_text, str) and level_text not in ["", "待解析"]:
            levels = [level_text]

        lat_min = weather_info.get("latMin")
        lat_max = weather_info.get("latMax")
        lon_min = weather_info.get("lonMin")
        lon_max = weather_info.get("lonMax")

        bbox = None
        if None not in [lat_min, lat_max, lon_min, lon_max]:
            bbox = {
                "south": lat_min,
                "north": lat_max,
                "west": lon_min,
                "east": lon_max
            }

        basic_result["variables"] = variable_items
        basic_result["times"] = times
        basic_result["levels"] = levels
        basic_result["bbox"] = bbox

        # 4. 替换 placeholder extra
        basic_result["extra"] = {
            "status": "parsed",
            "parser": "adapters.gfs_adapter.process_file",
            "main_variable": weather_info.get("mainVariable"),
            "main_variable_name": weather_info.get("mainVariableName"),
            "var_type": weather_info.get("varType"),
            "raw_unit": weather_info.get("rawUnit"),
            "display_unit": weather_info.get("displayUnit"),
            "conversion": weather_info.get("conversion"),
            "group_index": weather_info.get("groupIndex"),
            "file_size_mb": weather_info.get("fileSizeMB"),
            "lat_min": lat_min,
            "lat_max": lat_max,
            "lon_min": lon_min,
            "lon_max": lon_max,
        }

        return basic_result

    weather_info["basic_result"] = basic_result
    return weather_info