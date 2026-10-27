from io import BytesIO
import json
from pathlib import Path
import re
import secrets
import sys
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from .deck import Deck, safe_path
from .geometry import SurfaceModel
from .loads import LoadSpec, export_model, preview


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


def create_app(token: str):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.model = None
    app.state.model_id = None
    app.state.importing = False
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
            if length and (not length.isdigit() or int(length) > 512 * 1024 * 1024):
                return JSONResponse({"detail": "文件组不能超过 512 MB"}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        return response

    @app.exception_handler(ValueError)
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
        return {"id": app.state.model_id, "filename": model.deck.root,
                "nodes": len(model.points), "elements": len(model.deck.mesh.elements), "faces": len(model.faces),
                "bodies": [{"id": key, "name": value} for key, value in model.body_names.items()],
                "cases": [{"id": i, "name": c.name, "initial": c.initial} for i, c in enumerate(model.deck.cases)],
                "follow": model.deck.follow, "materials": model.deck.materials, "missing_materials": model.deck.missing_materials,
                "source_sha256": model.deck.digest}

    @app.get("/api/model")
    def get_model(request: Request):
        current(request)
        return metadata()

    @app.post("/api/model")
    async def import_model(request: Request):
        if app.state.importing:
            raise HTTPException(409, "模型正在读取，请稍候")
        app.state.importing = True
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
                model = await run_in_threadpool(lambda: SurfaceModel(Deck(files, root)))
            app.state.model = model
            app.state.model_id = uuid4().hex
            return metadata()
        finally:
            app.state.importing = False

    @app.get("/api/mesh")
    def mesh(request: Request):
        model = current(request)
        return {"points": model.points.ravel().tolist(), "triangles": model.triangles.ravel().tolist(),
                "triangle_faces": model.triangle_faces.tolist(), "body_ids": model.body_ids.tolist(),
                "edges": model.edges.ravel().tolist(), "edge_bodies": model.edge_bodies.tolist()}

    @app.post("/api/select")
    def select(request: Request, data: SelectionInput):
        return {"faces": current(request).select(data.seed, data.angle)}

    @app.post("/api/preview")
    def calculate(request: Request, data: LoadInput):
        return preview(current(request), LoadSpec(**data.model_dump()))

    @app.post("/api/export")
    def export(request: Request, data: ExportInput):
        model = current(request)
        files, report = export_model(model, [LoadSpec(**item.model_dump()) for item in data.loads])
        output = BytesIO()
        with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
            for name, content in files.items():
                archive.writestr(name, content)
            archive.writestr("sfmarc-loads-report.json", json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
        return Response(output.getvalue(), media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="sfmarc_loads.zip"'})

    @app.get("/")
    def index():
        return FileResponse(web / "index.html")

    app.mount("/assets", StaticFiles(directory=web / "assets", check_dir=False), name="assets")
    return app
