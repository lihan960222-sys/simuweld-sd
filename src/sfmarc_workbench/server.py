import csv
from pathlib import Path
import re
import secrets
import sys
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from .deck import Deck, safe_path
from .background import BackgroundModel
from .geometry import SurfaceModel
from . import routes_face_load, routes_weld_paths, routes_thermal_cycle, routes_local_trial
from .routes_thermal_cycle import TEMPERATURE_FILE_LIMIT


def create_app(token: str):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.model = None
    app.state.model_id = None
    app.state.importing = False
    app.state.import_progress = {"active": False, "stage": "", "completed": None, "total": None, "unit": ""}
    external = getattr(sys, "_sd_root", None)
    web = Path(external) / "shared" / "web" if external else Path(__file__).resolve().parents[2] / "dist"

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

    routes_local_trial.register(app, load_model)
    routes_thermal_cycle.register(app, load_model)
    routes_face_load.register(app, load_model)
    routes_weld_paths.register(app)

    @app.get("/api/modules")
    def module_versions():
        return {"interface": 1, "versions": getattr(sys, "_sd_versions", {})}

    @app.get("/")
    def index():
        return FileResponse(web / "index.html")

    @app.get("/icon.svg")
    @app.get("/icon.ico")
    def icon(request: Request):
        return FileResponse(web / request.url.path[1:])

    app.mount("/assets", StaticFiles(directory=web / "assets", check_dir=False), name="assets")
    app.mount("/shared", StaticFiles(directory=web if external else web / "shared", check_dir=False), name="shared")
    for ident in ("face_load", "weld_paths", "thermal_cycle", "local_trial"):
        folder = Path(external) / "modules" / ident / "web" if external else web / "modules" / ident
        app.mount("/modules/" + ident, StaticFiles(directory=folder, check_dir=False), name=ident)
    return app
