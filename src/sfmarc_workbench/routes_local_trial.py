from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from starlette.concurrency import run_in_threadpool

from .local_trial import LocalTrial
from .local_trial_export import export_local_trial


class LocalTrialInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    length_unit: str


class LocalTrialBox(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    min: list[float] = Field(min_length=3, max_length=3)
    max: list[float] = Field(min_length=3, max_length=3)


class LocalTrialSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    model_id: str
    path_id: StrictInt
    start_mm: float = Field(ge=0)
    end_mm: float = Field(ge=0)
    wrap: bool = False
    tolerance_mm: float = Field(default=0.01, gt=0)
    box: LocalTrialBox
    keep_whole: list[str]
    trim_start_mm: float = Field(default=0, ge=0)
    trim_end_mm: float = Field(default=0, ge=0)
    included_path_ids: list[StrictInt] | None = None


class LocalTrialConstraint(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=80)
    node_ids: list[StrictInt] = Field(min_length=1)
    axes: list[bool] = Field(min_length=3, max_length=3)


class LocalTrialGroup(LocalTrialSpec):
    name: str = Field(min_length=1, max_length=80)
    constraints: list[LocalTrialConstraint] = Field(default_factory=list)


class LocalTrialExportInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    model_id: str
    groups: list[LocalTrialGroup] = Field(min_length=1, max_length=100)
    cooling_limit_c: float = Field(default=26, gt=25)
    max_cooling_s: float = Field(default=60, gt=0)


def register(app, load_model):
    app.state.local_trial = None

    def selected_trial(request):
        model = load_model(request)
        value = app.state.local_trial
        if value is None or value.model is not model:
            raise HTTPException(409, "请读取当前模型的固有应变法焊路")
        return value

    @app.post("/api/local-trial")
    async def read_local_trial(request: Request, data: LocalTrialInput):
        model = load_model(request)
        value = await run_in_threadpool(LocalTrial, model, data.length_unit)
        if app.state.model is not model:
            raise HTTPException(409, "模型已更新，请重新读取")
        app.state.local_trial = value
        return await run_in_threadpool(value.metadata)

    @app.post("/api/local-trial/preview")
    async def preview_local_trial(request: Request, data: LocalTrialSpec):
        value = selected_trial(request)
        if data.model_id != value.model_id:
            raise HTTPException(409, "模型单位或模型已更新，请重新设置固有应变法")
        result = await run_in_threadpool(value.preview, data.model_dump(exclude_none=True))
        if app.state.model is not value.model or app.state.local_trial is not value:
            raise HTTPException(409, "模型已更新，请重新预览")
        return result

    @app.post("/api/local-trial/export")
    async def export_trial(request: Request, data: LocalTrialExportInput):
        value = selected_trial(request)
        content = await run_in_threadpool(export_local_trial, value, data.model_dump(exclude_none=True))
        if app.state.model is not value.model or app.state.local_trial is not value:
            raise HTTPException(409, "模型已更新，请重新导出")
        return Response(content, media_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="local_trial.zip"'})
