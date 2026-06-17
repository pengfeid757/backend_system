# 气象数据展示后台

FastAPI 后台。当前版本只做一件事：接收前端上传的气象文件，按业务类型保存到 `data/`，调用对应 adapter 生成 `meta.json`，并把展示面板需要的数据返回给前端。

## 目录结构

```text
backend/
├─ main.py                  # FastAPI 入口
├─ adapters/                # 6 个业务处理脚本
│  ├─ base.py
│  ├─ cma_adapter.py
│  ├─ era5_adapter.py
│  ├─ gfs_adapter.py
│  ├─ himawari_adapter.py
│  ├─ radar_adapter.py
│  └─ wrf_adapter.py
├─ data/                    # 上传文件、meta.json、后续 PNG 都放这里
│  ├─ CMA/
│  ├─ ERA5/
│  ├─ GFS/
│  ├─ Himawari/
│  ├─ Radar/
│  └─ WRF/
├─ services/                # 前端按数据类型读取展示数据
│  ├─ cma_service.py
│  ├─ era5_service.py
│  ├─ gfs_service.py
│  ├─ himawari_service.py
│  ├─ radar_service.py
│  └─ wrf_service.py
├─ samples/                 # 小样例文件
├─ requirements.txt
└─ README.md
```

## 当前接口

```text
GET  /api/health
POST /api/files/parse
GET  /api/display/{business_type}
```

前端当前调用：

```text
POST http://127.0.0.1:8002/api/files/parse
```

上传字段名必须是 `file`。

前端点击数据类型时可调用：

```text
GET http://127.0.0.1:8002/api/display/ERA5
```

返回对应业务目录下最新的 `meta_json` 和 PNG 路径。

## 业务识别规则

后端会根据文件名或扩展名判断业务类型：

```text
CMA       -> 文件名包含 cma
ERA5      -> 文件名包含 era5，或 .nc 默认归入 ERA5
GFS       -> 文件名包含 gfs，或 .grib/.grib2
Himawari  -> 文件名包含 himawari/hsd，或 .hsd
Radar     -> 文件名包含 radar/cinrad，或 .cinrad/.radar
WRF       -> 文件名包含 wrf
```

如果 `.nc` 文件属于 WRF，文件名中需要包含 `wrf`。

## Adapter 规则

每个成员只改自己负责的 adapter：

```text
CMA       -> adapters/cma_adapter.py
ERA5      -> adapters/era5_adapter.py
GFS       -> adapters/gfs_adapter.py
Himawari  -> adapters/himawari_adapter.py
Radar     -> adapters/radar_adapter.py
WRF       -> adapters/wrf_adapter.py
```

每个 adapter 对外保留这个函数：

```python
def process_file(file_path: str, data_type: str) -> dict:
    ...
```

当前 adapter 只是占位实现：会写入 `原文件名.meta.json`，但还没有真实解析和 PNG 生成逻辑。

## 启动

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload --host 127.0.0.1 --port 8002
```

访问：

```text
http://127.0.0.1:8002/docs
```

## 返回给前端的数据

`/api/files/parse` 返回统一格式：

```json
{
  "code": 0,
  "data": {
    "file_name": "era5_sample.nc",
    "directory": "D:/weather_prediction_system/backend/data/ERA5/",
    "business_type": "ERA5",
    "meta": {},
    "weather_info": {}
  },
  "message": "success"
}
```

前端当前使用：

```text
data.file_name
data.directory
data.weather_info
```

## 协作注意

- 不提交大体积气象数据。
- 小样例放 `samples/`。
- 上传或处理后的文件按业务放入 `data/{业务名}/`。
- 公共字段不够用时，先放到 meta 的 `extra` 中。
