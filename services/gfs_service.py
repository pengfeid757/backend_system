import json
from pathlib import Path
from typing import Any


DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "GFS"


def get_display_data() -> dict[str, Any]:
    # 前端点击 GFS 类型时调用该函数，读取 GFS 目录下的 meta.json 和 PNG。
    meta_files = sorted(DATA_DIR.glob("*.meta.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    png_files = sorted(DATA_DIR.glob("*.png"), key=lambda item: item.stat().st_mtime, reverse=True)

    meta_json = None
    if meta_files:
        with meta_files[0].open("r", encoding="utf-8") as file:
            meta_json = json.load(file)

    return {
        "business_type": "GFS",
        "meta_file": str(meta_files[0]).replace("\\", "/") if meta_files else None,
        "meta_json": meta_json,
        "png": str(png_files[0]).replace("\\", "/") if png_files else None,
        "png_files": [str(path).replace("\\", "/") for path in png_files],
    }
