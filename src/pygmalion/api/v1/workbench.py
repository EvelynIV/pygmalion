from __future__ import annotations

from fastapi import APIRouter, Request

from ...schemas.canvas import CanvasDocument, FitRequest, SvgImportRequest
from ...schemas.document import DocumentRequest
from ...servicer import workbench as service
from ..dependencies import get_context

router = APIRouter(prefix="/api/workbench")


@router.post("/preview")
def preview_document(data: CanvasDocument | DocumentRequest, fast: bool = True):
    return service.preview_document(data, fast)


@router.post("/write")
def write_document(request: Request, data: CanvasDocument | DocumentRequest):
    return service.write_document(get_context(request), data)


@router.post("/table-fit")
def fit_document_table(data: FitRequest):
    return service.fit_document_table(data)


@router.post("/import-svg")
def import_vector(data: SvgImportRequest):
    return service.import_vector(data)


@router.post("/validate")
def validate_document(data: CanvasDocument):
    """只校验文档结构和矢量几何，不依赖字体或启动机器。"""
    return {"valid": True}
