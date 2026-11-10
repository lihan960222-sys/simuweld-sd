from fastapi import Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from .welding import WeldPlan, generate_script, read_paths


def register(app):
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
