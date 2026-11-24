"""服务调用白名单校验。

纯函数、无 Home Assistant 依赖，便于脱离 HA 单元测试。
"""

from __future__ import annotations

# 这些 target 语法能绕过 entity_id 白名单，必须拒绝
FORBIDDEN_KEYS = ("target", "area_id", "device_id", "floor_id", "label_id")


def validate_service(allowed: list[str], payload: dict, has_service) -> tuple[str, str, dict]:
    """校验一次 call_service，通过则返回 (domain, name, body)，否则抛 ValueError。"""
    service = payload.get("service") or ""
    body = dict(payload.get("data") or {})

    if "." not in service:
        raise ValueError("服务格式不正确")
    domain, name = service.split(".", 1)

    for key in FORBIDDEN_KEYS:
        if key in body:
            raise ValueError("不支持按区域/设备/标签调用")

    target = body.get("entity_id")
    if isinstance(target, list):
        ids = target
    elif isinstance(target, str) and target:
        ids = [target]
    else:
        raise ValueError("缺少实体")

    if not has_service(domain, name):
        raise ValueError(f"服务不存在: {service}")

    allowed_set = set(allowed)
    allowed_domains = {entity_id.split(".")[0] for entity_id in allowed_set}
    if domain not in allowed_domains:
        raise ValueError(f"服务未授权: {service}")

    for entity_id in ids:
        if entity_id not in allowed_set:
            raise ValueError(f"实体未授权: {entity_id}")

    return domain, name, body
