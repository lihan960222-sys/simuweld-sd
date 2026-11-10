from io import TextIOWrapper
import xml.etree.ElementTree as ET

from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from .thermal import ThermalConversion, material_temperature

TEMPERATURE_FILE_LIMIT = 10 * 1024**3


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
    tolerance_c: float = Field(default=5, gt=0, le=100)


def register(app, load_model):
    app.state.thermal = None
    app.state.thermal_importing = False
    app.state.thermal_sampling = None

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
        value = selected_conversion(request, data.sampling_id)
        job = (value.model, object())
        app.state.thermal_sampling = job
        try:
            code = await run_in_threadpool(value.create_script, data.room_c, data.melting_c, data.tolerance_c)
            if app.state.model is not value.model or app.state.thermal is not value:
                raise HTTPException(409, "模型已更新，请为当前 DAT 重新生成脚本")
            return Response(code, media_type="text/x-python; charset=utf-8",
                            headers={"Content-Disposition": 'attachment; filename="create_thermal_cycle.py"'})
        finally:
            if app.state.thermal_sampling is job:
                app.state.thermal_sampling = None
