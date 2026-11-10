from io import BytesIO
import json
from zipfile import ZipFile, ZIP_DEFLATED

from fastapi import Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt

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


def register(app, load_model):
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
