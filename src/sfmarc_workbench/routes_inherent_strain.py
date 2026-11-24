from hashlib import sha256
import json
from typing import Literal
import xml.etree.ElementTree as ET

from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from .inherent_strain import TemperatureJob
from .plate_material import Material


class RevisionInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    model_id: str
    revision: int = Field(ge=0)


class PreviewInput(RevisionInput):
    length_unit: Literal['m', 'mm']
    spacing_mm: float | None = Field(default=None, gt=0, le=100)
    weld_spacing_mm: float | None = Field(default=None, gt=0, le=100)
    refine_distance_mm: float | None = Field(default=None, gt=0, le=100)


class JobInput(RevisionInput):
    job_id: str


class CalculationInput(JobInput):
    mechanics: bool = True
    staged: bool = False
    dt_s: float | None = Field(default=None, gt=0, le=1)
    end_s: float | None = Field(default=None, gt=0)
    record_interval_s: float | None = Field(default=None, gt=0)
    ambient_c: float | None = Field(default=None, gt=-273.15)
    initial_c: float | None = Field(default=None, gt=-273.15)
    convection_h: float | None = Field(default=None, ge=0)
    emissivity: float | None = Field(default=None, ge=0, le=1)
    contact_h: float | None = Field(default=None, ge=0)
    gap_h: float | None = Field(default=None, ge=0)
    weld_h: float | None = Field(default=None, ge=0)
    fixture_h: float | None = Field(default=None, ge=0)
    fixture_c: float | None = Field(default=None, gt=-273.15)


def cancel_current(app):
    job = getattr(app.state, 'inherent_job', None)
    if job is not None:
        job.cancel.set()
    app.state.inherent_job = None
    app.state.inherent_material = None
    app.state.inherent_revision = -1


def register(app, load_model):
    cancel_current(app)

    def current(request, model_id=None, revision=None, job_id=None):
        model = load_model(request)
        if model_id is not None and model_id != app.state.model_id:
            raise HTTPException(409, '模型已更新，请重新准备网格')
        if revision is not None and revision < app.state.inherent_revision:
            raise HTTPException(409, '输入已更新，请使用当前参数')
        job = app.state.inherent_job
        if job_id is not None and (job is None or job.job_id != job_id or job.model is not model):
            raise HTTPException(409, '任务已失效，请重新准备网格')
        return model, job

    def available(job):
        if getattr(app.state, 'inherent_exporting', False):
            raise HTTPException(409, '结果正在导出，请等待导出完成')
        worker = getattr(app.state, 'inherent_worker', job)
        if worker is not None and worker.thread is not None and worker.thread.is_alive():
            raise HTTPException(409, '当前任务正在执行，请取消并等待当前阶段结束')

    @app.post('/api/inherent-strain/invalidate')
    async def invalidate(request: Request, data: RevisionInput):
        current(request, data.model_id, data.revision)
        cancel_current(app)
        app.state.inherent_revision = data.revision
        return {'invalidated': True, 'revision': data.revision}

    @app.post('/api/inherent-strain/material')
    async def material(request: Request):
        model = load_model(request)
        form = await request.form(max_part_size=16 * 1024 ** 2)
        settings = RevisionInput.model_validate_json(json.dumps({'model_id': form.get('model_id'), 'revision': int(form.get('revision', '-1'))}))
        _, job = current(request, settings.model_id, settings.revision)
        available(job)
        file = form.get('material')
        if not isinstance(file, UploadFile) or not file.filename.lower().endswith('.xmt'):
            raise HTTPException(400, '请选择 XMT 材料文件')
        content = await file.read(16 * 1024 ** 2 + 1)
        if len(content) > 16 * 1024 ** 2:
            raise HTTPException(413, 'XMT 材料不能超过 16 MB')
        value = await run_in_threadpool(Material.from_xmt, content)
        root = ET.fromstring(content)
        info = dict(name=root.findtext('name') or file.filename, filename=file.filename, sha256=sha256(content).hexdigest(),
                    temperature_min_c=value.temperature_min - 273.15, temperature_max_c=value.temperature_max - 273.15)
        if (app.state.model is not model or settings.revision < app.state.inherent_revision
                or app.state.inherent_job is not job):
            raise HTTPException(409, '模型或参数已更新')
        available(job)
        app.state.inherent_revision = settings.revision
        app.state.inherent_material = (value, info)
        if job is not None:
            job.reset_stages()
            job.frames = []
            job.peak = None
            job.mechanical_result = None
            job.material, job.material_info = value, info
            job.update(frames=0)
            if job.preview is not None:
                job.update(status='ready', stage='材料已读取')
        return info

    @app.post('/api/inherent-strain/prepare')
    async def prepare(request: Request, data: PreviewInput):
        model, old = current(request, data.model_id, data.revision)
        available(old)
        job = TemperatureJob(model, data.model_id, data.revision, data.model_dump())
        material = app.state.inherent_material
        if material is not None:
            job.material, job.material_info = material
        app.state.inherent_revision = data.revision
        app.state.inherent_job = job
        app.state.inherent_worker = job
        job.run(job.prepare, operation='prepare')
        return job.status()

    @app.get('/api/inherent-strain/status')
    async def status(request: Request, job_id: str):
        _, job = current(request, job_id=job_id)
        return job.status()

    @app.get('/api/inherent-strain/grid')
    async def grid(request: Request, job_id: str):
        _, job = current(request, job_id=job_id)
        if job.preview is None:
            raise HTTPException(409, '网格尚未准备完成')
        return job.preview

    @app.post('/api/inherent-strain/start')
    async def start(request: Request, data: CalculationInput):
        _, job = current(request, data.model_id, data.revision, data.job_id)
        available(job)
        if data.revision != job.revision or job.grid is None or job.preview is None or job.material is None:
            raise HTTPException(409, '请使用当前参数准备网格并上传 XMT 材料')
        job.material.enthalpy((job.preview['defaults']['ambient_c'] if data.ambient_c is None else data.ambient_c) + 273.15)
        job.material.enthalpy((job.preview['defaults']['initial_c'] if data.initial_c is None else data.initial_c) + 273.15)
        job.run(lambda: job.calculate(data.model_dump()), operation='calculate')
        return job.status()

    @app.post('/api/inherent-strain/advance/{step}')
    async def advance(request: Request, step: Literal['strain', 'displacement'], data: JobInput):
        _, job = current(request, data.model_id, data.revision, data.job_id)
        available(job)
        required = 'thermal_done' if step == 'strain' else 'strain_done'
        if job.state['status'] != required or data.revision != job.revision:
            raise HTTPException(409, '请先完成上一阶段计算')
        action = job.solve_strain if step == 'strain' else job.solve_staged_displacement
        job.run(action, operation='calculate')
        return job.status()

    @app.get('/api/inherent-strain/strain')
    async def strain(request: Request, job_id: str):
        _, job = current(request, job_id=job_id)
        state = job.strain_result
        if state is None:
            raise HTTPException(409, '尚无固有应变结果')
        return dict(xx=state.plastic[:, 0].tolist(), yy=state.plastic[:, 1].tolist(),
                    equivalent=state.equivalent.tolist())

    @app.post('/api/inherent-strain/cancel')
    async def cancel(request: Request, data: JobInput):
        _, job = current(request, data.model_id, data.revision, data.job_id)
        if job.thread is not None and job.thread.is_alive():
            job.cancel.set()
            job.update(status='cancelling', stage='正在停止，等待当前操作结束')
        return job.status()

    @app.get('/api/inherent-strain/frame')
    async def frame(request: Request, job_id: str, index: int):
        _, job = current(request, job_id=job_id)
        with job.lock:
            if index < 0 or index >= len(job.frames):
                raise HTTPException(404, '温度帧尚未保存')
            time, values = job.frames[index]
            return dict(job_id=job_id, index=index, time_s=time, temperature_c=values.tolist())

    @app.get('/api/inherent-strain/peak')
    async def peak(request: Request, job_id: str):
        _, job = current(request, job_id=job_id)
        with job.lock:
            if job.peak is None:
                raise HTTPException(409, '尚无温度计算结果')
            return dict(job_id=job_id, temperature_c=job.peak.tolist())

    @app.get('/api/inherent-strain/export')
    async def export(request: Request, job_id: str):
        _, job = current(request, job_id=job_id)
        available(job)
        if not job.frames:
            raise HTTPException(409, '当前任务尚无温度帧')
        app.state.inherent_exporting = True
        try:
            content = await run_in_threadpool(job.export)
        finally:
            app.state.inherent_exporting = False
        current(request, job_id=job_id)
        return Response(content, media_type='application/zip', headers={'Content-Disposition': 'attachment; filename="temperature.zip"'})

    @app.get('/api/inherent-strain/mechanics')
    async def mechanics(request: Request, job_id: str):
        _, job = current(request, job_id=job_id)
        with job.lock:
            if job.mechanical_result is None:
                raise HTTPException(409, '机械结果尚未完成')
            return job.mechanical_result
