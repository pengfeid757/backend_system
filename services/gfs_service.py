import json
from pathlib import Path
from typing import Any, Optional

from adapters.gfs_adapter import process_file


# 当前文件位置：
# backend_system/services/gfs_service.py
# parents[1] 就是 backend_system
BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data" / "GFS"


def _to_web_path(path: Optional[Path]) -> Optional[str]:
    """
    把 Windows 路径里的反斜杠转成前端更容易处理的格式。
    """
    if path is None:
        return None
    return str(path).replace("\\", "/")


def _list_grib_files() -> list[Path]:
    """
    查找 GFS 目录下的 GRIB/GRIB2 文件。
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    files = []

    for suffix in ["*.grib", "*.grb", "*.grib2"]:
        files.extend(DATA_DIR.glob(suffix))

    files = sorted(files, key=lambda item: item.stat().st_mtime, reverse=True)

    return files


def _list_meta_files() -> list[Path]:
    """
    查找 GFS 目录下的 meta.json 文件。
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    return sorted(
        DATA_DIR.glob("*.meta.json"),
        key=lambda item: item.stat().st_mtime,
        reverse=True
    )


def _list_png_files() -> list[Path]:
    """
    查找 GFS 目录下的 PNG 文件。
    目前第一版可以没有 PNG，返回空列表也没问题。
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    return sorted(
        DATA_DIR.glob("*.png"),
        key=lambda item: item.stat().st_mtime,
        reverse=True
    )


def _find_meta_for_grib(grib_file: Path) -> Path:
    """
    process_basic_file 当前生成的 meta 文件一般是：
    053031.grib.meta.json
    """
    return grib_file.with_name(grib_file.name + ".meta.json")


def _read_json(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _ensure_latest_meta() -> tuple[Optional[Path], Optional[dict[str, Any]], Optional[Path]]:
    """
    核心逻辑：

    1. 找最新 GRIB 文件
    2. 检查是否已有对应 meta.json
    3. 没有就调用 gfs_adapter.process_file 自动解析并生成
    4. 返回 meta_path、meta_json、source_grib
    """
    grib_files = _list_grib_files()

    if not grib_files:
        return None, None, None

    latest_grib = grib_files[0]
    expected_meta = _find_meta_for_grib(latest_grib)

    # 如果 meta 不存在，或者 meta 比 GRIB 老，就重新解析
    need_parse = True

    if expected_meta.exists():
        try:
            need_parse = expected_meta.stat().st_mtime < latest_grib.stat().st_mtime
        except Exception:
            need_parse = True

    if need_parse:
        process_file(str(latest_grib), data_type="GFS")

    meta_json = _read_json(expected_meta)

    # 兜底：如果 adapter 生成的 meta 路径不是 expected_meta，就找最新 meta
    if meta_json is None:
        meta_files = _list_meta_files()
        if meta_files:
            expected_meta = meta_files[0]
            meta_json = _read_json(expected_meta)

    return expected_meta if expected_meta.exists() else None, meta_json, latest_grib


def get_display_data() -> dict[str, Any]:
    """
    前端点击 GFS 类型时调用该函数。

    返回内容：
    - 最新 GRIB 文件
    - 最新 meta.json
    - meta_json 内容
    - PNG 文件列表
    - weather_info，方便前端直接展示
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    meta_path, meta_json, latest_grib = _ensure_latest_meta()
    png_files = _list_png_files()
    grib_files = _list_grib_files()
    meta_files = _list_meta_files()

    weather_info = None
    if isinstance(meta_json, dict):
        weather_info = meta_json.get("weather_info")

    return {
        "business_type": "GFS",
        "data_type": "GFS",
        "status": "ok" if latest_grib else "no_data",

        "message": "GFS 数据读取成功" if latest_grib else "data/GFS 目录下暂无 GRIB/GRIB2 文件",

        "source_file": _to_web_path(latest_grib),
        "source_files": [_to_web_path(path) for path in grib_files],

        "meta_file": _to_web_path(meta_path),
        "meta_files": [_to_web_path(path) for path in meta_files],
        "meta_json": meta_json,

        "weather_info": weather_info,

        "png": _to_web_path(png_files[0]) if png_files else None,
        "png_files": [_to_web_path(path) for path in png_files],
    }