from io import BytesIO, TextIOWrapper
import csv
import json
from pathlib import Path
import re
import secrets
import sys
import xml.etree.ElementTree as ET
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from .deck import Deck, safe_path
from .background import BackgroundModel
from .geometry import SurfaceModel
from .loads import LoadSpec, export_model, preview
from .welding import WeldPlan, generate_script, read_paths
from .thermal import ThermalConversion, material_temperature

TEMPERATURE_FILE_LIMIT = 10 * 1024**3


class LoadInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    faces: list[StrictInt]
    magnitude: float = Field(gt=0)
    mode: str
    cases: list[StrictInt]
    direction: list[float] = [0, 0, -1]
    length_unit: str = "mm"
    force_unit: str = "N"
    pressure_unit: str = "MPa"
    name: str = Field(default="面载荷", max_length=80)


class ExportInput(BaseModel):
    loads: list[LoadInput] = Field(min_length=1, max_length=100)


class SelectionInput(BaseModel):
    seed: StrictInt = Field(ge=0)
    angle: float = Field(ge=0, le=180, allow_inf_nan=False)


class ThermalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    length_unit: str
    sampling: dict | None = None


class ThermalSelectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sampling_id: str = Field(min_length=1, max_length=64)


class ThermalSamplingInput(ThermalSelectionInput):
    sampling: dict


class ThermalScriptInput(ThermalSelectionInput):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    room_c: float = Field(gt=-273.15)
    melting_c: float = Field(gt=-273.15)
    maximum_mm: float = Field(default=2, gt=0, le=10000)
    tolerance_c: float = Field(default=5, gt=0, le=100)
    allow_partial: bool = False


def create_app(token: str):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.model = None
    app.state.model_id = None
    app.state.importing = False
    app.state.import_progress = {"active": False, "stage": "", "completed": None, "total": None, "unit": ""}
    app.state.thermal = None
    app.state.thermal_importing = False
    app.state.thermal_sampling = None
    web = Path(sys._MEIPASS) / "web" if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2] / "dist"

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = request.headers.get("host", "")
        if not re.fullmatch(r"127\.0\.0\.1(?::\d+)?", host):
            return JSONResponse({"detail": "仅允许本机地址访问"}, status_code=403)
        if request.headers.get("origin") not in (None, f"http://{host}"):
            return JSONResponse({"detail": "请求来源不允许"}, status_code=403)
        if request.url.path.startswith("/api/"):
            supplied = request.headers.get("x-session-token", "")
            if not secrets.compare_digest(supplied, token):
                return JSONResponse({"detail": "请使用启动程序打开工具"}, status_code=401)
            length = request.headers.get("content-length")
            temperature_upload = request.url.path == "/api/thermal/temperatures"
            request_limit = TEMPERATURE_FILE_LIMIT + 1024**2 if temperature_upload else 512 * 1024 * 1024
            if length and (not length.isdigit() or int(length) > request_limit):
                detail = "温度 CSV 不能超过 10 GB" if temperature_upload else "文件组不能超过 512 MB"
                return JSONResponse({"detail": detail}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        return response

    @app.exception_handler(ValueError)
    @app.exception_handler(csv.Error)
    async def invalid_input(request: Request, error: ValueError):
        return JSONResponse({"detail": str(error)}, status_code=400)

    def current(request):
        if app.state.model is None:
            raise HTTPException(409, "请导入模型")
        if request.headers.get("x-model-id") != app.state.model_id:
            raise HTTPException(409, "模型已更新，请重新导入当前页面的模型")
        return app.state.model

    def metadata():
        model = app.state.model
        if isinstance(model, BackgroundModel):
            return {"id": app.state.model_id, "filename": model.filename, "nodes": len(model.points),
                    "elements": model.element_count, "faces": len(model.faces), "supports_loads": False,
                    "length_unit": model.length_unit, "cases": [],
                    "bodies": [{"id": key, "name": value, "volume": model.body_volumes[key]}
                               for key, value in model.body_names.items()]}
        return {"id": app.state.model_id, "filename": model.deck.root,
                "supports_loads": True,
                "nodes": len(model.points), "elements": len(model.deck.mesh.elements), "faces": len(model.faces),
                "bodies": [{"id": key, "name": value, "volume": model.body_volumes[key]}
                           for key, value in model.body_names.items()],
                "cases": [{"id": i, "name": c.name, "initial": c.initial} for i, c in enumerate(model.deck.cases)],
                "follow": model.deck.follow, "materials": model.deck.materials, "missing_materials": model.deck.missing_materials,
                "source_sha256": model.deck.digest}

    @app.get("/api/model")
    def get_model(request: Request):
        current(request)
        return metadata()

    def import_progress(stage, completed=None, total=None, unit=""):
        app.state.import_progress = {"active": app.state.importing, "stage": stage,
                                     "completed": completed, "total": total, "unit": unit}

    @app.get("/api/import-progress")
    async def get_import_progress():
        return app.state.import_progress

    @app.post("/api/model/clear")
    async def clear_model(request: Request):
        current(request)
        if app.state.importing:
            raise HTTPException(409, "模型正在读取，请稍候")
        app.state.model = None
        app.state.model_id = None
        app.state.thermal = None
        return {"cleared": True}

    @app.post("/api/model")
    async def import_model(request: Request):
        if app.state.importing:
            raise HTTPException(409, "模型正在读取，请稍候")
        app.state.importing = True
        import_progress("接收模型文件")
        try:
            async with request.form(max_files=512, max_fields=4, max_part_size=512*1024*1024) as form:
                root = form.get("root")
                if not isinstance(root, str):
                    raise ValueError("请选择主 DAT 文件")
                files = {}
                total = 0
                for uploaded in form.getlist("files"):
                    if not isinstance(uploaded, UploadFile) or not uploaded.filename:
                        raise ValueError("文件内容无效")
                    name = safe_path(uploaded.filename)
                    if name in files:
                        raise ValueError(f"文件路径重复：{name}")
                    data = await uploaded.read()
                    total += len(data)
                    if total > 512*1024*1024:
                        raise ValueError("文件组不能超过 512 MB")
                    files[name] = data
                    import_progress("读取模型文件", len(files), len(form.getlist("files")), "个文件")
                model = await run_in_threadpool(lambda: SurfaceModel(Deck(files, root, import_progress), import_progress))
            app.state.model = model
            app.state.model_id = uuid4().hex
            app.state.thermal = None
            import_progress("模型读取完成", len(model.points), len(model.points), "个节点")
            return metadata()
        finally:
            app.state.importing = False
            app.state.import_progress = {**app.state.import_progress, "active": False}

    @app.get("/api/mesh")
    def mesh(request: Request):
        model = current(request)
        payload = {"points": model.points.ravel().tolist(), "triangles": model.triangles.ravel().tolist(),
                "triangle_faces": model.triangle_faces.tolist(), "body_ids": model.body_ids.tolist(),
                "edges": model.edges.ravel().tolist(), "edge_bodies": model.edge_bodies.tolist()}
        return payload

    @app.post("/api/background")
    async def import_background(request: Request):
        if app.state.importing:
            raise HTTPException(409, "模型正在读取，请稍候")
        app.state.importing = True
        import_progress("接收模型文件")
        try:
            async with request.form(max_files=1, max_fields=1, max_part_size=512*1024*1024) as form:
                uploaded = form.get("file")
                if not isinstance(uploaded, UploadFile) or not uploaded.filename:
                    raise ValueError("请选择网格或 STEP 文件")
                data = await uploaded.read(512*1024*1024+1)
                if len(data) > 512*1024*1024:
                    raise ValueError("文件不能超过 512 MB")
                file_format = form.get("format") or Path(uploaded.filename).suffix[1:].lower()
                model = await run_in_threadpool(BackgroundModel, data, uploaded.filename, file_format, import_progress)
            app.state.model = model
            app.state.model_id = uuid4().hex
            app.state.thermal = None
            import_progress("模型读取完成", len(model.points), len(model.points), "个节点")
            return metadata()
        finally:
            app.state.importing = False
            app.state.import_progress = {**app.state.import_progress, "active": False}

    def load_model(request):
        model = current(request)
        if isinstance(model, BackgroundModel):
            raise HTTPException(409, "此模型用于焊路几何背景；面载荷需要导入 Marc DAT")
        return model

    def conversion(request):
        model = load_model(request)
        value = app.state.thermal
        if value is None or value.model is not model:
            raise HTTPException(409, "请读取当前 DAT 的焊路")
        if app.state.thermal_importing:
            raise HTTPException(409, "温度数据正在读取，请稍候")
        if app.state.thermal_sampling and app.state.thermal_sampling[0] is model:
            raise HTTPException(409, "采样节点正在计算，请稍候")
        return value

    def selected_conversion(request, sampling_id):
        value = conversion(request)
        if sampling_id != value.sampling_id:
            raise HTTPException(409, "采样参数已更新，请使用当前预览重新生成温度导出脚本")
        return value

    @app.post("/api/thermal")
    async def prepare_thermal(request: Request, data: ThermalInput):
        model = load_model(request)
        if app.state.thermal_importing:
            raise HTTPException(409, "温度数据正在读取，请稍候")
        if app.state.thermal_sampling and app.state.thermal_sampling[0] is model:
            raise HTTPException(409, "采样节点正在计算，请稍候")
        job = (model, object())
        app.state.thermal_sampling = job
        app.state.thermal = None
        try:
            value = await run_in_threadpool(ThermalConversion, model, data.length_unit, data.sampling)
            if app.state.model is not model:
                raise HTTPException(409, "模型已更新，请重新读取焊路")
            app.state.thermal = value
            return value.metadata()
        finally:
            if app.state.thermal_sampling is job:
                app.state.thermal_sampling = None

    @app.post("/api/thermal/resample")
    async def resample_thermal(request: Request, data: ThermalSamplingInput):
        value = selected_conversion(request, data.sampling_id)
        job = (value.model, object())
        app.state.thermal_sampling = job
        value.exported = False
        value.temperatures = None
        value.summary = None
        try:
            await run_in_threadpool(value.resample, data.sampling)
            if app.state.model is not value.model or app.state.thermal is not value:
                raise HTTPException(409, "模型已更新，请重新读取焊路")
            return value.metadata()
        finally:
            if app.state.thermal_sampling is job:
                app.state.thermal_sampling = None

    @app.post("/api/thermal/export")
    async def temperature_script(request: Request, data: ThermalSelectionInput):
        return Response(selected_conversion(request, data.sampling_id).export_script(), media_type="text/x-python; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="export_node_temperatures.py"'})

    @app.post("/api/thermal/temperatures")
    async def import_temperatures(request: Request):
        value = conversion(request)
        app.state.thermal_importing = True
        try:
            async with request.form(max_files=1, max_fields=1, max_part_size=1024*1024) as form:
                if form.get("sampling_id") != value.sampling_id:
                    raise HTTPException(409, "温度数据导入关联的采样预览已更新，请重新生成导出脚本")
                uploaded = form.get("file")
                if not isinstance(uploaded, UploadFile) or not uploaded.filename:
                    raise ValueError("请选择温度 CSV")
                if uploaded.size is not None and uploaded.size > TEMPERATURE_FILE_LIMIT:
                    raise HTTPException(413, "温度 CSV 不能超过 10 GB")
                text = TextIOWrapper(uploaded.file, encoding="utf-8-sig", newline="")
                try:
                    result = await run_in_threadpool(value.import_temperatures, text)
                finally:
                    text.detach()
            if app.state.model is not value.model:
                raise HTTPException(409, "模型已更新，请为当前 DAT 重新导入温度数据")
            return result
        finally:
            app.state.thermal_importing = False

    @app.post("/api/thermal/material")
    async def import_thermal_material(request: Request):
        conversion(request)
        async with request.form(max_files=1, max_fields=0, max_part_size=20*1024*1024) as form:
            uploaded = form.get("file")
            if not isinstance(uploaded, UploadFile) or not uploaded.filename:
                raise ValueError("请选择 Simufact XML 材料文件")
            data = await uploaded.read(20*1024*1024+1)
            if len(data) > 20*1024*1024:
                raise ValueError("材料文件不能超过 20 MB")
            try:
                return material_temperature(data)
            except ET.ParseError as error:
                raise ValueError("材料文件需要为包含 melting_point 的 Simufact XML") from error

    @app.post("/api/thermal/create")
    async def create_thermal_script(request: Request, data: ThermalScriptInput):
        code = selected_conversion(request, data.sampling_id).create_script(data.room_c, data.melting_c, data.maximum_mm,
                                                 data.tolerance_c, data.allow_partial)
        return Response(code, media_type="text/x-python; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="create_thermal_cycle.py"'})

    @app.post("/api/select")
    def select(request: Request, data: SelectionInput):
        return {"faces": load_model(request).select(data.seed, data.angle)}

    @app.post("/api/preview")
    def calculate(request: Request, data: LoadInput):
        return preview(load_model(request), LoadSpec(**data.model_dump()))

    @app.post("/api/export")
    def export(request: Request, data: ExportInput):
        model = load_model(request)
        files, report = export_model(model, [LoadSpec(**item.model_dump()) for item in data.loads])
        output = BytesIO()
        with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
            for name, content in files.items():
                archive.writestr(name, content)
            archive.writestr("sfmarc-loads-report.json", json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
        return Response(output.getvalue(), media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="sfmarc_loads.zip"'})

    @app.post("/api/weld-paths")
    async def import_weld_paths(request: Request):
        paths = []
        total = 0
        async with request.form(max_files=256, max_fields=1, max_part_size=20*1024*1024) as form:
            unit = form.get("unit")
            for uploaded in form.getlist("files"):
                if not isinstance(uploaded, UploadFile) or not uploaded.filename:
                    raise ValueError("请选择 CSV 文件")
                data = await uploaded.read(20*1024*1024+1)
                total += len(data)
                if total > 20*1024*1024:
                    raise ValueError("CSV 文件组不能超过 20 MB")
                paths.extend(await run_in_threadpool(read_paths, data, uploaded.filename, unit))
        if not paths:
            raise ValueError("请选择 CSV 文件")
        if len(paths) > 1000 or sum(len(path["points"]) for path in paths) > 100000:
            raise ValueError("单次导入不能超过 1000 条路径或 10 万个点")
        return {"paths": paths}

    @app.post("/api/weld-script")
    def export_weld_script(data: WeldPlan):
        return Response(generate_script(data), media_type="text/x-python; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="generate_weld_paths.py"'})

    @app.get("/")
    def index():
        return FileResponse(web / "index.html")

    @app.get("/icon.svg")
    @app.get("/icon.ico")
    def icon(request: Request):
        return FileResponse(web / request.url.path[1:])

    app.mount("/assets", StaticFiles(directory=web / "assets", check_dir=False), name="assets")
    return app
