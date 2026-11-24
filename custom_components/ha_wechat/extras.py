"""P1 详情增强：日程、待办、天气预报。

这些数据要么来自 HA 的服务返回值，要么来自实体属性；统一在这里做异步读取并
裁剪成小程序能直接渲染的结构。图片、相册、摄像头快照不在范围内。
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from typing import Any

from . import controls

_LOGGER = logging.getLogger(__name__)


CALENDAR_MONTHS_BACK = 6
CALENDAR_MONTHS_FORWARD = 6
CALENDAR_LIMIT = 800
FORECAST_LIMIT = 8
CACHE_TTL = 180

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _zone(time_zone: str | None):
    if not time_zone:
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(time_zone)
    except (KeyError, ValueError):
        return None


def _day_label(value: Any, time_zone: str | None) -> str:
    """把预报/日程时间转成「今天 / 明天 / 8月12日」这种短标签。"""
    stamp = _parse_time(value)
    if stamp is None:
        return str(value or "")
    tz = _zone(time_zone)
    if stamp.tzinfo is not None and tz is not None:
        stamp = stamp.astimezone(tz)
    today = datetime.now(tz).date() if tz else datetime.now().date()
    delta = (stamp.date() - today).days
    if delta == 0:
        return "今天"
    if delta == 1:
        return "明天"
    if delta == 2:
        return "后天"
    prefix = "" if stamp.year == today.year else f"{stamp.year}年"
    return f"{prefix}{stamp.month}月{stamp.day}日"


def _raw_value(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value or "").strip()


def _local_date_text(value: Any, time_zone: str | None) -> str:
    """日程在 HA 时区里的日期，供月历按天落点，不依赖手机本地时区。"""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    text = str(value or "").strip()
    if not text:
        return ""
    if _DATE_ONLY.match(text[:10]) and len(text) == 10:
        return text
    stamp = _parse_time(value)
    if stamp is None:
        return text[:10] if _DATE_ONLY.match(text[:10]) else ""
    tz = _zone(time_zone)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=tz or timezone.utc)
    elif tz is not None:
        stamp = stamp.astimezone(tz)
    return stamp.date().isoformat()


def _local_clock_text(value: Any, time_zone: str | None) -> str:
    stamp = _parse_time(value)
    if stamp is None:
        return ""
    tz = _zone(time_zone)
    if stamp.tzinfo is not None and tz is not None:
        stamp = stamp.astimezone(tz)
    return f"{stamp.hour:02d}:{stamp.minute:02d}"


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + month - 1 + delta
    return index // 12, index % 12 + 1


def _calendar_window(now: datetime, time_zone: str | None) -> tuple[datetime, datetime, str, str]:
    """月历需要的事件窗口：前 6 个月到后 6 个月，首尾月都完整覆盖。"""
    tz = _zone(time_zone) or timezone.utc
    local_now = now.astimezone(tz) if now.tzinfo is not None else now.replace(tzinfo=tz)
    base_year, base_month = local_now.year, local_now.month
    start_year, start_month = _shift_month(base_year, base_month, -CALENDAR_MONTHS_BACK)
    last_year, last_month = _shift_month(base_year, base_month, CALENDAR_MONTHS_FORWARD)
    end_year, end_month = _shift_month(last_year, last_month, 1)
    start_at = datetime.combine(date(start_year, start_month, 1), datetime.min.time(), tzinfo=tz)
    end_at = datetime.combine(date(end_year, end_month, 1), datetime.min.time(), tzinfo=tz)
    return (
        start_at,
        end_at,
        f"{start_year:04d}-{start_month:02d}",
        f"{last_year:04d}-{last_month:02d}",
    )


def _event_row(item: dict, time_zone: str | None) -> dict | None:
    if not isinstance(item, dict):
        return None
    start = item.get("start") or item.get("start_time") or item.get("start_date_time")
    end = item.get("end") or item.get("end_time") or item.get("end_date_time")
    summary = str(item.get("summary") or item.get("message") or item.get("title") or "").strip()
    if not summary and not start:
        return None

    start_text = _raw_value(start)
    all_day = bool(item.get("all_day")) or (
        isinstance(start, date)
        and not isinstance(start, datetime)
    ) or bool(_DATE_ONLY.match(start_text))
    when = ""
    if start:
        when = (
            _day_label(start, time_zone)
            if all_day
            else controls.format_temporal(start, "datetime", time_zone=time_zone)
        )
    if end and start and not all_day:
        end_text = controls.format_temporal(end, "datetime", time_zone=time_zone)
        if end_text and end_text != when:
            when = f"{when} - {end_text}" if when else end_text

    return {
        "uid": str(item.get("uid") or item.get("id") or "").strip(),
        "title": summary or "未命名日程",
        "time": when,
        "start_value": start_text,
        "end_value": _raw_value(end),
        "start_date": _local_date_text(start, time_zone),
        "end_date": _local_date_text(end, time_zone),
        "start_clock": "" if all_day else _local_clock_text(start, time_zone),
        "end_clock": "" if all_day else _local_clock_text(end, time_zone),
        "location": str(item.get("location") or "").strip(),
        "description": str(item.get("description") or "").strip()[:120],
        "allDay": all_day,
        "all_day": all_day,
    }


def _attrs_event(attrs: dict) -> dict:
    start = attrs.get("start_time") or attrs.get("start")
    end = attrs.get("end_time") or attrs.get("end")
    summary = attrs.get("message") or attrs.get("summary") or attrs.get("friendly_name") or "日程"
    return {
        "start": start,
        "end": end,
        "summary": summary,
        "location": attrs.get("location"),
        "description": attrs.get("description") or attrs.get("event_type"),
        "all_day": bool(attrs.get("all_day")),
    }


async def _async_cache(cache: dict | None, key: str, loader):
    if cache is None:
        return await loader()
    item = cache.get(key)
    now = datetime.now(timezone.utc).timestamp()
    if item and now - item[0] < CACHE_TTL:
        return item[1]
    value = await loader()
    cache[key] = (now, value)
    return value


async def _async_forecast(hass, entity_id: str, attrs: dict) -> list[dict]:
    raw = attrs.get("forecast")
    if not isinstance(raw, list) or not raw:
        raw = []
        # 新版 HA 把预报从属性移到了 weather.get_forecasts 服务。
        for kind in ("daily", "hourly"):
            try:
                result = await hass.services.async_call(
                    "weather",
                    "get_forecasts",
                    {"entity_id": entity_id, "type": kind},
                    blocking=True,
                    return_response=True,
                )
            except Exception:  # noqa: BLE001 - 服务不存在/集成不支持时退回属性
                continue
            payload = result.get(entity_id) if isinstance(result, dict) else None
            if isinstance(payload, dict):
                raw = payload.get("forecast") or []
            elif isinstance(result, dict):
                raw = result.get("forecast") or []
            if raw:
                break

    rows: list[dict] = []
    for item in raw[:FORECAST_LIMIT]:
        if not isinstance(item, dict):
            continue
        when = item.get("datetime") or item.get("date") or item.get("time")
        condition = item.get("condition") or ""
        rows.append(
            {
                "day": _day_label(when, hass.config.time_zone),
                "time": controls.format_temporal(when, "datetime", time_zone=hass.config.time_zone) or "",
                "condition": str(condition or ""),
                "temperature": item.get("temperature"),
                "low": item.get("templow"),
                "precipitation": item.get("precipitation_probability"),
                "humidity": item.get("humidity"),
                "wind": item.get("wind_speed"),
            }
        )
    return rows


async def _async_calendar(hass, entity_id: str, attrs: dict) -> dict:
    rows: list[dict] = []
    now = datetime.now(timezone.utc)
    # supported_features 在实体状态属性里；调用方传空时自己补读一次，
    # 单元测试和旧调用路径都能拿到写能力位。
    state_obj = hass.states.get(entity_id)
    state_attrs = state_obj.attributes if state_obj is not None else {}
    passed_attrs = attrs or {}
    attrs = dict(state_attrs or {})
    attrs.update(passed_attrs)
    # 与 CalendarEntityFeature 对齐：CREATE_EVENT=1、DELETE_EVENT=2、UPDATE_EVENT=4。
    # 不 import HA，单元测试也能跑。
    features = int((attrs or {}).get("supported_features") or 0)
    start_at, end_at, first_month, last_month = _calendar_window(now, hass.config.time_zone)
    try:
        result = await hass.services.async_call(
            "calendar",
            "get_events",
            {
                "entity_id": entity_id,
                "start_date_time": start_at.isoformat(),
                "end_date_time": end_at.isoformat(),
            },
            blocking=True,
            return_response=True,
        )
        payload = None
        if isinstance(result, dict):
            payload = result.get(entity_id)
            if payload is None:
                for value in result.values():
                    if isinstance(value, dict) and "events" in value:
                        payload = value
                        break
            if payload is None and "events" in result:
                payload = result
        events = (payload or {}).get("events") if isinstance(payload, dict) else []
        for item in events or []:
            row = _event_row(item, hass.config.time_zone)
            if row:
                rows.append(row)
    except Exception:  # noqa: BLE001 - 旧版本没有 get_events，用属性兜底
        pass

    if not rows:
        row = _event_row(_attrs_event(attrs), hass.config.time_zone) if attrs else None
        if row:
            rows.append(row)
    rows.sort(key=lambda row: (row["start_date"], row["start_clock"], row["title"]))
    return {
        "entity_id": entity_id,
        "events": rows[:CALENDAR_LIMIT],
        "total": len(rows),
        "first_month": first_month,
        "last_month": last_month,
        "can_create": bool(features & 1),
        "can_delete": bool(features & 2),
        "can_update": bool(features & 4),
    }


def _todo_due_value(value: Any) -> str:
    """保留待办截止时间的原始值，编辑时不能只拿「今天/明天」这种标签。"""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value or "").strip()


async def _async_todo(hass, entity_id: str) -> dict:
    items: list[dict] = []
    state_obj = hass.states.get(entity_id)
    attrs = state_obj.attributes if state_obj is not None else {}
    # 与 TodoListEntityFeature 对齐，不 import HA，单元测试也能跑。
    features = int(attrs.get("supported_features") or 0)
    try:
        result = await hass.services.async_call(
            "todo",
            "get_items",
            {"entity_id": entity_id},
            blocking=True,
            return_response=True,
        )
        payload = result.get(entity_id) if isinstance(result, dict) else None
        if payload is None and isinstance(result, dict):
            if "items" in result:
                payload = result
            else:
                for value in result.values():
                    if isinstance(value, dict) and "items" in value:
                        payload = value
                        break
        raw = (payload or {}).get("items") if isinstance(payload, dict) else []
        for item in raw or []:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "needs_action").lower()
            completed = status == "completed"
            items.append(
                {
                    "uid": item.get("uid") or "",
                    "summary": str(item.get("summary") or "").strip() or "待办",
                    "status": "completed" if completed else "needs_action",
                    "completed": completed,
                    "due": _day_label(item.get("due"), hass.config.time_zone),
                    "due_value": _todo_due_value(item.get("due")),
                    "description": str(item.get("description") or "").strip(),
                }
            )
    except Exception:  # noqa: BLE001 - 集成未加载时只显示数量
        pass

    # 未完成在前、已完成在后，和 HA 待办卡的阅读顺序一致
    items.sort(key=lambda row: row["completed"])
    pending = sum(1 for row in items if not row["completed"])
    return {
        "entity_id": entity_id,
        "items": items,
        "total": len(items),
        "pending": pending,
        "completed": len(items) - pending,
        "features": features,
        "can_create": bool(features & 1),
        "can_delete": bool(features & 2),
        "can_update": bool(features & 4),
        "can_due_date": bool(features & 16),
        "can_due_datetime": bool(features & 32),
        "can_description": bool(features & 64),
    }


async def async_enrich(hass, helper, entity_id: str, detail: dict, cache: dict | None = None) -> dict:
    """给详情补上 P1 数据；失败时保持原详情，不影响实体页打开。"""
    domain = controls.domain_of(entity_id)
    state_obj = hass.states.get(entity_id)
    attrs = state_obj.attributes if state_obj is not None else {}

    if domain == "weather":
        async def load_weather():
            rows = await _async_forecast(hass, entity_id, attrs)
            return {
                "condition": str(state_obj.state if state_obj is not None else ""),
                "temperature": attrs.get("temperature"),
                "unit": attrs.get("temperature_unit") or "°C",
                "humidity": attrs.get("humidity"),
                "pressure": attrs.get("pressure"),
                "wind_speed": attrs.get("wind_speed"),
                "wind_unit": attrs.get("wind_speed_unit") or "",
                "forecast": rows,
            }

        try:
            detail["weather"] = await _async_cache(cache, f"weather:{entity_id}", load_weather)
            detail["forecast"] = detail["weather"].get("forecast") or []
        except Exception as err:  # noqa: BLE001 - 扩展失败不影响基础详情
            _LOGGER.debug("读取天气详情失败 %s: %s", entity_id, err)
    elif domain == "calendar":
        try:
            calendar = await _async_cache(
                cache, f"calendar:{entity_id}", lambda: _async_calendar(hass, entity_id, attrs)
            )
            detail["calendar"] = calendar
            detail["agenda"] = calendar.get("events") or []
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("读取日程失败 %s: %s", entity_id, err)
    elif domain == "todo":
        try:
            detail["todo"] = await _async_cache(
                cache, f"todo:{entity_id}", lambda: _async_todo(hass, entity_id)
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("读取待办失败 %s: %s", entity_id, err)
    elif domain == "event":
        try:
            row = _event_row(_attrs_event(attrs), hass.config.time_zone)
            detail["agenda"] = [row] if row else []
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("读取事件失败 %s: %s", entity_id, err)

    if domain in ("weather", "calendar", "todo", "event"):
        # 这些域的数据已经在专用卡片里展示，属性里再放一遍会出现
        # “forecast: [object Object]”这种没法读的行。
        detail["attrs"] = []

    return detail
