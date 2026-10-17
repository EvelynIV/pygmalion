from __future__ import annotations

import logging
import os
import socket

from ..config import CONFIG, ROOT
from ..writing import prepare as preparation

logger = logging.getLogger(__name__)


def local_ips() -> list[str]:
    """
    本机所有可用的局域网 IP，用来告诉成员该访问哪个地址。

    热点场景下会有多个网卡（有线、无线、热点），逐个列出来比只显示一个
    更实用 —— 用户能一眼找到自己手机连的那个网段。
    """
    ips: set[str] = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


def setup():
    """
    操作相关的只读信息：笔尖该放哪个角、整张纸占机器的哪段范围。

    公开接口 —— 管理页要用它显示对位提示。不含任何机器控制能力，
    也不暴露路径。改原点角之后这里的文字会自动跟着变，不用改前端。
    """
    from ..machine import coordinates as gcode

    cfg = preparation.build_configs()
    tf = gcode.PageToMachine(cfg["machine"], cfg["page"])
    x0, y0, x1, y1 = tf.bounds()
    page = cfg["page"]
    return {
        "ok": True,
        "origin_corner": "左上"
        if cfg["machine"].fixed_grid_origin
        else CONFIG["page"].get("origin_corner", "左上"),
        "origin_target": "刻度区域的左上角"
        if cfg["machine"].fixed_grid_origin
        else f"纸张的{CONFIG['page'].get('origin_corner', '左上')}角",
        "fixed_grid_origin": cfg["machine"].fixed_grid_origin,
        "hint": tf.page_origin_hint(),
        "page_mm": [page.width_mm, page.height_mm],
        "machine_bounds": [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)],
        "negative_travel": {
            "x": round(abs(min(x0, 0.0)), 1),
            "y": round(abs(min(y0, 0.0)), 1),
        },
    }


def list_fonts():
    """
    可用字库列表。公开接口 —— 提交页也要用它给作者选字体。
    只暴露名字、字数这类展示信息，不含任何路径。
    """
    from ..fonts import registry as font_registry

    available = font_registry.discover(ROOT)
    return {
        "ok": True,
        "default": font_registry.default_id(ROOT),
        "fonts": [f.to_dict() for f in available],
    }


def network():
    """热点地址，显示在管理页上方便告诉社团成员。"""
    port = int(os.getenv("PYGMALION_PORT", CONFIG["server"]["port"]))
    return {
        "ok": True,
        "port": port,
        "urls": [f"http://{ip}:{port}" for ip in local_ips()],
    }


def health():
    return {"status": "ok"}
