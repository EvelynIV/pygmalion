"""将共享文档数据转换成排版领域的纸张配置。"""

from ..schemas.canvas import CanvasDocument
from ..schemas.document import DocumentRequest
from .models import PageConfig


def page_config(document: CanvasDocument | DocumentRequest) -> PageConfig:
    width, height = (210, 297) if document.paper == "A4" else (148, 210)
    return PageConfig(width, height, *([document.margin_mm] * 4))
