"""设备操作错误，与网络协议无关。"""


class MachineError(Exception):
    def __init__(self, kind: str, payload: dict):
        self.kind = kind
        self.payload = payload
        super().__init__(payload.get("error", kind))
