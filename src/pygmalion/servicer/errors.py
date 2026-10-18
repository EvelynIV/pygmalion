"""服务操作错误；由 API 层映射到传输协议。"""


class ServiceError(Exception):
    def __init__(self, kind: str, payload: dict):
        self.kind = kind
        self.payload = payload
        super().__init__(payload.get("error", kind))
