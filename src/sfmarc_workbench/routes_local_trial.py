from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from starlette.concurrency import run_in_threadpool

from .local_trial import LocalTrial
from .local_trial_export import export_local_trial
from pathlib import Path
import json
import sys
from uuid import uuid4
from starlette.datastructures import UploadFile

from .deck import Deck
from .thermal_deck import read_weld_paths
from .local_result_archive import analyze_archive
from .local_abaqus_export import read_shell, export_abaqus, selected_paths
from .templates.shell_mapping import path_segments


class LocalTrialInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    length_unit: str


class LocalResultsScriptInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    process_name: str = Field(default="", max_length=200)
    final_time_s: float | None = Field(default=None, gt=0)
    cooling_limit_c: float = Field(default=26, gt=25)


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
    source_process: str = Field(default='', max_length=200)
    target_process: str = Field(default='', max_length=200)
    component_names: dict[str, str] = Field(default_factory=dict)


class LocalAnalysisInput(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    length_mm: float = Field(default=1., gt=0)
    bins: int = Field(default=16, ge=2, le=256)
    final_time_s: float | None = Field(default=None, gt=0)
    length_unit: str = 'm'


class AbaqusExportInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    job_name: str = Field(default='SD_Weld', min_length=1, max_length=60)
    model_id: str
    equivalent_id: str
    body_components: dict[str, str]
    path_order: list[StrictInt] | None = None
    reverse_path_ids: list[StrictInt] = Field(default_factory=list)


class AbaqusPreviewInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    filename: str = Field(min_length=1, max_length=200)
    length_unit: str = 'mm'


class LocalRecomputeInput(LocalAnalysisInput):
    dataset_id: str


def register(app, load_model):
    app.state.local_trial = None
    app.state.local_equivalent = None
    app.state.local_abaqus = None
    app.state.local_result_context = None
    app.state.local_result_progress = {'active': False, 'completed': 0, 'total': 0}

    @app.post("/api/local-trial/results-script")
    async def results_script(data: LocalResultsScriptInput):
        name = data.process_name.strip()
        if any(ord(character) < 32 for character in name):
            raise HTTPException(400, "算例名称不能包含控制字符。")
        template = Path(__file__).with_name("templates") / "local_results_export.py"
        content = ("PROCESS_NAME = " + repr(name) + "\nEXPORT_SETTINGS = "
                   + repr(data.model_dump(exclude={'process_name'})) + "\n\n" + template.read_text(encoding="utf-8"))
        return Response(content, media_type="text/x-python; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="export_local_results.py"'})

    @app.get('/api/local-trial/results-progress')
    async def result_progress():
        return app.state.local_result_progress

    @app.post('/api/local-trial/results')
    async def import_results(request: Request):
        if app.state.local_result_progress['active']:
            raise HTTPException(409, '结果正在计算，请稍候。')
        form = await request.form(max_part_size=512 * 1024 ** 2)
        settings = LocalAnalysisInput.model_validate_json(str(form.get('settings', '{}')))
        file = form.get('results')
        if not isinstance(file, UploadFile) or not file.filename.lower().endswith('.zip'):
            raise HTTPException(400, '请选择局部计算结果 ZIP。')
        root = Path(getattr(sys, '_sd_root', Path.cwd())) / 'work' / 'local-shell'
        root.mkdir(parents=True, exist_ok=True)
        path = root / (uuid4().hex + '.zip')
        previous = app.state.local_result_context
        app.state.local_result_context = None
        if previous is not None:
            previous['path'].unlink(missing_ok=True)
        app.state.local_equivalent = None
        app.state.local_result_progress = {'active': True, 'completed': 0, 'total': 0, 'stage': '保存结果文件'}
        keep_archive = False
        try:
            with path.open('xb') as stream:
                while chunk := await file.read(1024 ** 2):
                    stream.write(chunk)
            model = form.get('local_model')
            plan = None
            if isinstance(model, UploadFile):
                content = await model.read()
                plan = await run_in_threadpool(read_weld_paths, Deck({model.filename: content}, model.filename), settings.length_unit)
            def progress(completed, total):
                app.state.local_result_progress = {'active': True, 'completed': completed, 'total': total, 'stage': '读取积分点并计算截面等效量'}
            result = await run_in_threadpool(analyze_archive, path, root, settings.length_mm, settings.bins,
                                             settings.final_time_s, plan, progress)
            result['dataset_id'] = path.stem
            app.state.local_result_context = {'dataset_id': path.stem, 'path': path, 'plan': plan}
            app.state.local_equivalent = result
            keep_archive = True
            return result
        finally:
            if not keep_archive:
                path.unlink(missing_ok=True)
            app.state.local_result_progress = dict(app.state.local_result_progress, active=False)

    @app.post('/api/local-trial/results-recompute')
    async def recompute_results(settings: LocalRecomputeInput):
        context = app.state.local_result_context
        if context is None or settings.dataset_id != context['dataset_id']:
            raise HTTPException(409, '结果文件已更换，请重新读取。')
        if app.state.local_result_progress['active']:
            raise HTTPException(409, '结果正在计算，请稍候。')
        app.state.local_equivalent = None
        app.state.local_result_progress = {'active': True, 'completed': 0, 'total': 0, 'stage': '更新代表区间'}
        def progress(completed, total):
            app.state.local_result_progress = {'active': True, 'completed': completed, 'total': total, 'stage': '更新截面等效量'}
        try:
            result = await run_in_threadpool(analyze_archive, context['path'], context['path'].parent,
                                             settings.length_mm, settings.bins, settings.final_time_s, context['plan'], progress)
            result['dataset_id'] = settings.dataset_id
            app.state.local_equivalent = result
            return result
        finally:
            app.state.local_result_progress = dict(app.state.local_result_progress, active=False)

    @app.get('/api/local-trial/equivalent')
    async def download_equivalent():
        if app.state.local_equivalent is None:
            raise HTTPException(409, '请导入结果并完成代表区间计算。')
        return Response(json.dumps(app.state.local_equivalent, ensure_ascii=False, allow_nan=False, indent=2),
                        media_type='application/json', headers={'Content-Disposition': 'attachment; filename="shell_equivalent.json"'})

    @app.post('/api/local-trial/abaqus-model')
    async def import_abaqus(request: Request):
        form = await request.form(max_part_size=512 * 1024 ** 2)
        file = form.get('model')
        if not isinstance(file, UploadFile) or not file.filename.lower().endswith('.inp'):
            raise HTTPException(400, '请选择整体 shell INP。')
        app.state.local_abaqus = None
        preview = form.get('preview')
        preview_data = await preview.read() if isinstance(preview, UploadFile) else None
        model = await run_in_threadpool(read_shell, await file.read(), file.filename, str(form.get('length_unit', 'mm')), preview_data)
        app.state.local_abaqus = model
        preview = dict(model['preview'])
        trial, equivalent = app.state.local_trial, app.state.local_equivalent
        if trial is not None and equivalent is not None:
            preview['segments'] = path_segments(trial.plan['paths'], equivalent['representative_length_mm'])
        return preview

    @app.post('/api/local-trial/abaqus-preview-script')
    async def preview_script(data: AbaqusPreviewInput):
        if Path(data.filename).name != data.filename or '/' in data.filename or '\\' in data.filename or data.length_unit not in ('m', 'mm'):
            raise HTTPException(400, 'INP 文件名或单位无效。')
        template = Path(__file__).with_name('templates') / 'export_abaqus_preview.py'
        content = ('# -*- coding: utf-8 -*-\nimport json\nSETTINGS = json.loads('
                   + repr(json.dumps(data.model_dump())) + ')\n' + template.read_text(encoding='utf-8'))
        return Response(content, media_type='text/x-python; charset=utf-8',
                        headers={'Content-Disposition': 'attachment; filename="export_abaqus_preview.py"'})

    @app.post('/api/local-trial/abaqus-export')
    async def create_abaqus(request: Request, data: AbaqusExportInput):
        trial = selected_trial(request)
        if data.model_id != trial.model_id:
            raise HTTPException(409, '来源焊路已修改，请重新确认。')
        if app.state.local_abaqus is None or app.state.local_equivalent is None:
            raise HTTPException(409, '请读取 shell 模型及代表数据。')
        if data.equivalent_id != app.state.local_equivalent['equivalent_id']:
            raise HTTPException(409, '代表数据已经更新，请使用当前结果重新导出。')
        content = await run_in_threadpool(export_abaqus, app.state.local_abaqus, app.state.local_equivalent,
                                          selected_paths(trial.plan['paths'], data.path_order, data.reverse_path_ids), data.body_components, data.job_name)
        return Response(content, media_type='application/zip',
                        headers={'Content-Disposition': 'attachment; filename="abaqus_weld.zip"'})

    def selected_trial(request):
        model = load_model(request)
        value = app.state.local_trial
        if value is None or value.model is not model:
            raise HTTPException(409, "请读取当前模型的局部试算焊路")
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
            raise HTTPException(409, "模型单位或模型已更新，请重新设置局部试算")
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
