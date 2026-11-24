"""端到端加密帧：AES-256-GCM + 单调计数器。

纯函数、仅依赖 cryptography，便于脱离 HA 做单元测试与跨语言互通验证。
"""

from __future__ import annotations

import json
import struct

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION = 5
DIR_APP = 1
DIR_HA = 2
HEADER = struct.Struct(">BIQ")
TAG_LEN = 16
AAD_PREFIX = b"ha-wechat-v5"


class FrameError(Exception):
    """帧不合法：版本不符、超长、计数回退或标签校验失败。"""


def _nonce(direction: int, counter: int) -> bytes:
    return struct.pack(">IQ", direction, counter)


def _aad(room_id: str, device_id: str) -> bytes:
    return AAD_PREFIX + bytes.fromhex(room_id) + bytes.fromhex(device_id)


def parse_header(raw: bytes) -> tuple[str, int]:
    """读出明文帧头，返回 (deviceId, counter)。"""
    if len(raw) < HEADER.size + TAG_LEN:
        raise FrameError("帧过短")
    ver, dev, counter = HEADER.unpack_from(raw)
    if ver != VERSION:
        raise FrameError("协议版本不符")
    return f"{dev:08x}", counter


def encode(direction: int, room_id: str, device_id: str, key_hex: str, counter: int, payload: dict) -> bytes:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    sealed = AESGCM(bytes.fromhex(key_hex)).encrypt(
        _nonce(direction, counter), body, _aad(room_id, device_id)
    )
    return HEADER.pack(VERSION, int(device_id, 16), counter) + sealed


def decode(room_id: str, device_id: str, key_hex: str, raw: bytes) -> tuple[dict, int]:
    """解密，返回 (payload, counter)；调用方需自行校验计数器单调性。"""
    device, counter = parse_header(raw)
    if device != device_id:
        raise FrameError("设备标识不符")
    try:
        body = AESGCM(bytes.fromhex(key_hex)).decrypt(
            _nonce(DIR_APP, counter), raw[HEADER.size :], _aad(room_id, device_id)
        )
    except Exception as err:  # cryptography 的 InvalidTag
        raise FrameError("解密失败") from err
    try:
        return json.loads(body), counter
    except ValueError as err:
        raise FrameError("载荷不是合法 JSON") from err


def decode_from_ha(room_id: str, device_id: str, key_hex: str, raw: bytes) -> tuple[dict, int]:
    """小程序侧使用：HA→小程序方向的帧。"""
    device, counter = parse_header(raw)
    if device != device_id:
        raise FrameError("设备标识不符")
    try:
        body = AESGCM(bytes.fromhex(key_hex)).decrypt(
            _nonce(DIR_HA, counter), raw[HEADER.size :], _aad(room_id, device_id)
        )
    except Exception as err:
        raise FrameError("解密失败") from err
    try:
        return json.loads(body), counter
    except ValueError as err:
        raise FrameError("载荷不是合法 JSON") from err
