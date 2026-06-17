from adapters.base import process_basic_file


def process_file(file_path: str, data_type: str = "WRF") -> dict:
    weather_info = {
        "source": "WRF",
        "product": "WRF 模式数据",
        "element": "NC 变量",
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
        "update": "待解析",
        "bars": [0, 0, 0, 0, 0],
        "trend": [0, 0, 0, 0, 0, 0, 0, 0],
    }
    return process_basic_file(file_path, data_type=data_type, file_format="NC", weather_info=weather_info)
