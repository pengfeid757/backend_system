import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def build_dataset_id(source_file: Path) -> str:
    return source_file.name.replace(".", "_")


def write_meta(meta_file: Path, meta: dict[str, Any]) -> None:
    meta_file.parent.mkdir(parents=True, exist_ok=True)

    with meta_file.open("w", encoding="utf-8") as file:
        json.dump(meta, file, ensure_ascii=False, indent=2)


def process_basic_file(
    file_path: str,
    data_type: str,
    file_format: str,
    weather_info: dict[str, Any],
) -> dict[str, Any]:
    source_file = Path(file_path).resolve()
    meta_file = source_file.with_name(f"{source_file.name}.meta.json")

    meta = {
        "dataset_id": build_dataset_id(source_file),
        "data_type": data_type,
        "file_format": file_format,
        "source_file": source_file.as_posix(),
        "meta_file": meta_file.as_posix(),
        "png_files": [],
        "variables": [],
        "times": [],
        "levels": [],
        "bbox": None,
        "weather_info": weather_info,
        "extra": {
            "status": "placeholder",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "note": "请在对应 adapter 中补充真实解析逻辑和 PNG 生成逻辑。",
        },
    }

    write_meta(meta_file, meta)
    return meta
