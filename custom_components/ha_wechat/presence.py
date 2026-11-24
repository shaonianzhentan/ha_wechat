"""设备在线状态判定。

纯函数、无 Home Assistant 依赖，便于单元测试。在线只看「已配对 + 最近有效帧
是否在窗口内」，不把 WebSocket 连接本身当成某台手机在线。
"""

from __future__ import annotations

ONLINE_WINDOW = 90


def is_device_online(device: dict | None, now: float, window: int = ONLINE_WINDOW) -> bool:
    if not device or not device.get("active"):
        return False
    try:
        last_seen = float(device.get("last_seen") or 0)
    except (TypeError, ValueError):
        return False
    return last_seen > 0 and (now - last_seen) <= window
