"""API 适配器共享的上下文注入。"""

from starlette.requests import HTTPConnection

from ..servicer.context import ServiceContext


def get_context(connection: HTTPConnection) -> ServiceContext:
    return connection.app.state.services
