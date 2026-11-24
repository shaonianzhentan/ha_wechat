"""控件模型：把实体属性翻译成前端可直接渲染的控件。

这个模块**不依赖 Home Assistant**，纯函数、可独立测试（见 tests/test_controls.py）。
它只回答一个问题：这个实体能被怎么控制？

为什么这样设计
--------------
旧的写法是「每个域一段 if」，加一个域要改后端 + 3 个前端文件。现在反过来：
后端输出控件列表，前端只按 `kind` 渲染。新增域 = 在 BUILDERS 里加一行，
小程序不用动。未知域走 `build_generic`，按常见属性推导，保证不白屏。

控件通用结构
------------
{
  "kind": "slider",            # 决定前端用哪种控件
  "key": "brightness",         # 稳定标识，用于去重和局部更新
  "label": "亮度",
  ...kind 专属字段...
  "service": "light.turn_on",  # 前端原样调用，不需要知道域映射
  "entity_id": "light.a",
  "params": {"brightness_pct": "%value%"}   # %value% 占位，前端替换
}

占位符：`%value%` 控件当前值；`%ratio%` 值/100（音量这类 0-1 参数）。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9 兜底
    ZoneInfo = None

# 状态算“开着”的集合（卡片高亮、开关方向都依赖它）
ON_STATES = frozenset(
    {"on", "open", "opening", "playing", "cleaning", "heat", "cool", "home", "active", "mowing"}
)

# 卡片上可以直接给开关的域
TOGGLE_DOMAINS = frozenset(
    {
        "light",
        "switch",
        "fan",
        "input_boolean",
        "automation",
        "humidifier",
        "media_player",
        "siren",
        "valve",
        "remote",
        "water_heater",
    }
)

# 一次性动作域：点卡片即执行，没有“开/关”概念
ACTION_DOMAINS = frozenset({"scene", "script", "button", "input_button"})

# 域 → 执行服务名
ACTION_SERVICES = {
    "scene": "turn_on",
    "script": "turn_on",
    "button": "press",
    "input_button": "press",
}

# 纯展示，不提供写操作
READ_ONLY_DOMAINS = frozenset(
    {
        "sensor",
        "binary_sensor",
        "weather",
        "sun",
        "zone",
        "person",
        "device_tracker",
        "proximity",
        "calendar",
        "event",
    }
)

# 卡片上直接把数值显示的域
VALUE_DOMAINS = frozenset({"sensor", "number", "input_number", "counter"})

HVAC_LABELS = {
    "cool": "制冷",
    "heat": "制热",
    "auto": "自动",
    "dry": "除湿",
    "fan_only": "送风",
    "heat_cool": "冷暖",
    "off": "关闭",
}

FAN_LABELS = {
    "auto": "自动",
    "low": "低",
    "medium": "中",
    "middle": "中",
    "high": "高",
    "silent": "静音",
    "turbo": "强力",
    "very_low": "最低",
    "very_high": "最高",
    "off": "关闭",
}

SWING_LABELS = {
    "off": "关闭",
    "on": "开",
    "vertical": "上下",
    "horizontal": "左右",
    "both": "上下+左右",
    "mid": "中",
    "middle": "中",
}

HVAC_ACTION_LABELS = {
    "heating": "制热",
    "cooling": "制冷",
    "drying": "除湿",
    "fan": "送风",
    "idle": "待机",
    "off": "关闭",
    "preheating": "预热",
    "defrosting": "除霜",
}

PRESET_LABELS = {    "auto": "自动",
    "silent": "静音",
    "eco": "节能",
    "sleep": "睡眠",
    "comfort": "舒适",
    "boost": "强力",    "normal": "标准",
    "away": "离家",
    "home": "在家",
    "none": "无",
}

# 支持颜色的灯用 color_name 下发，避免在前端做取色器
COLOR_LABELS = {
    "red": "红",
    "orange": "橙",
    "yellow": "黄",
    "green": "绿",
    "cyan": "青",
    "blue": "蓝",
    "purple": "紫",
    "pink": "粉",
    "white": "白",
}

COLOR_MODES = frozenset({"hs", "rgb", "rgbw", "rgbww", "xy"})

# 这些域的状态名在 HA 翻译表里往往缺失（或根本没有），在这里补一层中文。
# 表里没有的 domain/state 会返回 None，调用方继续走 HA 翻译。
STATE_LABELS = {
    "timer": {
        "active": "计时中",
        "paused": "已暂停",
        "idle": "未启动",
        "unknown": "未知",
        "unavailable": "不可用",
    },
    "update": {"on": "有新版本", "off": "已是最新"},
    "lawn_mower": {
        "mowing": "割草中",
        "paused": "已暂停",
        "docked": "已回充",
        "returning": "返回中",
        "error": "故障",
    },
    "vacuum": {
        "docked": "已回充",
        "cleaning": "清扫中",
        "returning": "返回中",
        "paused": "已暂停",
        "idle": "待机",
        "error": "故障",
    },
    "valve": {
        "open": "已打开",
        "closed": "已关闭",
        "opening": "打开中",
        "closing": "关闭中",
    },
    "lock": {
        "locked": "已上锁",
        "unlocked": "已解锁",
        "locking": "上锁中",
        "unlocking": "解锁中",
        "jammed": "卡住了",
        "open": "门未闭",
    },
    "alarm_control_panel": {
        "disarmed": "已撤防",
        "armed_home": "在家布防",
        "armed_away": "外出布防",
        "armed_night": "夜间布防",
        "armed_vacation": "度假布防",
        "armed_custom_bypass": "自定义布防",
        "arming": "布防中",
        "pending": "等待中",
        "triggered": "已触发",
    },
    "media_player": {
        "playing": "播放中",
        "paused": "已暂停",
        "idle": "待机",
        "off": "已关闭",
        "standby": "待机",
        "on": "已开启",
    },
    "remote": {
        "on": "已开启",
        "off": "已关闭",
        "unknown": "未知",
        "unavailable": "不可用",
    },
    "calendar": {
        "on": "有日程",
        "off": "无日程",
        "unknown": "未知",
        "unavailable": "不可用",
    },
    "todo": {
        "unknown": "未知",
        "unavailable": "不可用",
    },
}

# 属性里不需要展示给用户的（内部元数据）
HIDDEN_ATTRS = frozenset(
    {
        "friendly_name",
        "icon",
        "entity_picture",
        "supported_features",
        "supported_color_modes",
        "device_class",
        "attribution",
        "editable",
        "id",
        "options",
        "restored",
        "assumed_state",
        "icon_color",
        # input_datetime 的内部开关，控件已经表达清楚了
        "has_date",
        "has_time",
        "timestamp",
        # 灯的颜色模式/色温范围属于控件实现细节，详情里不再重复一遍
        "color_mode",
        "color_temp",
        "color_temp_kelvin",
        "min_color_temp_kelvin",
        "max_color_temp_kelvin",
        "hs_color",
        "rgb_color",
        "rgbw_color",
        "rgbww_color",
        "xy_color",
        "white",
        # 媒体播放器的内部/重复字段，控件里已经能看见
        "media_position_updated_at",
        "is_volume_muted",
        "volume_level",
        "source_list",
        "sound_mode_list",
        "sound_mode",
        "media_content_id",
        "media_content_type",
    }
)

# 这些属性的值是用户内容/设备名，不能把 on/off 之类的状态词硬翻
FREE_TEXT_ATTRS = frozenset(
    {
        "friendly_name",
        "name",
        "title",
        "message",
        "description",
        "location",
        "address",
        "release_summary",
        "changed_by",
        "source",
        "app_name",
        "brand",
        "model",
        "model_name",
        "serial_number",
        "media_title",
        "media_artist",
        "media_album_name",
        "media_album_artist",
        "media_series_title",
        "media_channel",
    }
)

# 「设备信息」里常见属性的中文名；没收录的键原样展示
ATTR_LABELS = {
    "color_mode": "色彩模式",
    "color_temp": "色温",
    "color_temp_kelvin": "色温(K)",
    "brightness": "亮度",
    "hs_color": "色相/饱和度",
    "rgb_color": "RGB",
    "color_name": "颜色",
    "min_color_temp_kelvin": "最暖色温",
    "max_color_temp_kelvin": "最冷色温",
    "current_position": "当前开合度",
    "current_tilt_position": "当前叶片角度",
    "temperature": "目标温度",
    "current_temperature": "当前温度",
    "humidity": "目标湿度",
    "current_humidity": "当前湿度",
    "hvac_mode": "模式",
    "hvac_modes": "可用模式",
    "hvac_action": "运行状态",
    "fan_mode": "风速",
    "fan_modes": "可用风速",
    "swing_mode": "摆风",
    "swing_modes": "可用摆风",
    "preset_mode": "预设",
    "preset_modes": "可用预设",
    "aux_heat": "辅热",
    "battery_level": "电量",
    "battery": "电量",
    "voltage": "电压",
    "linkquality": "信号质量",
    "unit_of_measurement": "单位",
    "media_title": "播放内容",
    "media_artist": "艺人",
    "media_album_name": "专辑",
    "media_duration": "总时长",
    "media_position": "播放位置",
    "source": "音源",
    "source_list": "可用音源",
    "volume_level": "音量",
    "percentage": "百分比",
    "charger_type": "充电状态",
    "latitude": "纬度",
    "longitude": "经度",
    "gps_accuracy": "定位精度",
    "radius": "半径",
    "last_seen": "最后上报",
    "last_changed": "最后变化",
    "last_updated": "最后更新",
    "latest_version": "最新版本",
    "installed_version": "当前版本",
    "min_temp": "最低温度",
    "max_temp": "最高温度",
    "target_temp_step": "温度步进",
    "target_temp_low": "温度下限",
    "target_temp_high": "温度上限",
    "min_humidity": "最低湿度",
    "max_humidity": "最高湿度",
    "fan_speed": "风扇转速",
    "fan_speed_list": "可用转速",
    "swing_horizontal_mode": "水平摆风",
    "swing_horizontal_modes": "可用水平摆风",
    "oscillating": "是否摇头",
    "speed_count": "档位数",
    "supported_speed_count": "支持档位",
    "effect_list": "可用灯效",
    "effect": "灯效",
}


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def domain_of(entity_id: str) -> str:
    return entity_id.split(".", 1)[0]


def num(value):
    """安全转数字：拒绝 bool，转不了就 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# 卡片/详情里要按中文习惯显示的日期时间域
TEMPORAL_DOMAINS = frozenset({"date", "time", "datetime", "input_datetime"})
TEMPORAL_CLASSES = frozenset({"date", "time", "timestamp"})
_DATE_RE = re.compile(
    r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{1,2})(?::\d{1,2})?)?"
)
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{1,2})(?::\d{1,2}(?:\.\d+)?)?$")


def _time_zone(time_zone: str | None):
    if not time_zone or ZoneInfo is None:
        return None
    try:
        return ZoneInfo(str(time_zone))
    except (KeyError, ValueError, TypeError):
        return None


def _parse_iso(text: str) -> datetime | None:
    """尽量解析 HA 的 ISO 时间串，兼容 Z / 空格 / 毫秒 / 时区偏移。"""
    raw = text.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, datetime) else None


def format_temporal(
    value,
    domain: str = "",
    device_class: str = "",
    time_zone: str | None = None,
    has_date: bool | None = None,
    has_time: bool | None = None,
) -> str | None:
    """把 HA 的日期/时间状态转成中文短格式。

    HA 的时间状态不止一种写法，这里按「实体类型 + device_class + input_datetime 的
    has_date/has_time」决定显示形态，而不是只看字符串里有没有时间：

    - date / device_class=date                 2026-11-25              -> 11月25日
    - time / device_class=time                 07:00:00                -> 07:00
    - datetime / device_class=timestamp        2026-11-17T09:53:01Z    -> 11月17日 17:53
    - input_datetime 只有日期                  has_date/has_time       -> 11月17日
    - input_datetime 只有时间                                          -> 06:30
    - input_datetime 日期+时间                                         -> 11月17日 06:30

    带时区的 timestamp 会先转成 HA 配置的时区，不再依赖宿主机本地时区。
    """
    text = str(value or "").strip()
    if not text or text.lower() in ("unknown", "unavailable", "none", "null", "-"):
        return None

    tz = _time_zone(time_zone)
    now = datetime.now(tz) if tz else datetime.now()

    # 先判断这次应该显示日期、时间，还是两者都显示
    if domain == "date" or device_class == "date":
        shape = "date"
    elif domain == "time" or device_class == "time":
        shape = "time"
    elif domain == "datetime" or device_class == "timestamp":
        shape = "datetime"
    elif domain == "input_datetime":
        if has_date is False:
            shape = "time"
        elif has_time is False:
            shape = "date"
        else:
            shape = "auto"
    else:
        shape = "auto"

    # 纯时间状态：07:00 / 07:00:00 / 07:00:00.000
    match = _TIME_RE.match(text)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if shape == "date":
            return None
        return f"{hour:02d}:{minute:02d}"

    # 数字时间戳（有些集成把 timestamp 直接存成秒数）
    if shape == "datetime":
        try:
            epoch = float(text)
        except (TypeError, ValueError):
            epoch = None
        if epoch is not None and epoch > 1_000_000_000:
            stamp = datetime.fromtimestamp(epoch, tz) if tz else datetime.fromtimestamp(epoch)
            prefix = "" if stamp.year == now.year else f"{stamp.year}年"
            return f"{prefix}{stamp.month}月{stamp.day}日 {stamp.hour:02d}:{stamp.minute:02d}"

    stamp = _parse_iso(text)

    if stamp is None:
        match = _DATE_RE.match(text)
        if not match:
            return None
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
        hour = int(match.group(4)) if match.group(4) is not None else None
        minute = int(match.group(5)) if match.group(5) is not None else None
        has_clock = hour is not None
    else:
        # 带时区的状态按 HA 时区换算；naive 状态视为 HA 本地时间，不再二次换算
        if stamp.tzinfo is not None:
            stamp = stamp.astimezone(tz) if tz else stamp.astimezone()
        year, month, day = stamp.year, stamp.month, stamp.day
        hour, minute = stamp.hour, stamp.minute
        has_clock = bool(re.search(r"[T ]\d{1,2}:\d{2}", text))

    if shape == "time":
        return f"{hour:02d}:{minute:02d}" if hour is not None else None

    prefix = "" if year == now.year else f"{year}年"
    text_out = f"{prefix}{month}月{day}日"
    if shape != "date" and has_clock and hour is not None:
        text_out += f" {hour:02d}:{minute:02d}"
    return text_out



def service(entity_id: str, name: str, **params) -> dict:
    """组装服务调用，params 里的 None 会被丢掉。"""
    return {
        "service": f"{domain_of(entity_id)}.{name}",
        "entity_id": entity_id,
        "params": {k: v for k, v in params.items() if v is not None},
    }


def _slider(
    entity_id, key, label, value, low, high, step, unit, name, param,
    presets=None, mmss=False, total=None,
):
    control = {
        "kind": "slider",
        "key": key,
        "label": label,
        "value": value,
        "min": low,
        "max": high,
        "step": step,
        "unit": unit,
        **service(entity_id, name, **{param: "%value%"}),
    }
    if presets:
        # 常用档位由后端给，前端照着画，不需要知道哪些滑条该有快捷键
        control["presets"] = [{"label": text, "value": v} for text, v in presets]
    if mmss:
        # 秒数按 00:00 显示，total 是总时长（进度条右侧那句）
        control["mmss"] = True
        if total:
            control["total"] = int(total)
    return control


def _switch(entity_id, key, label, on, name, param):
    """布尔能力（辅热、摇头）：params 里给的是「点一下之后要变成的值」。"""
    return {
        "kind": "switch",
        "key": key,
        "label": label,
        "on": bool(on),
        **service(entity_id, name, **{param: not on}),
    }


def _elapsed(attrs: dict):
    """播放进度：HA 只在状态变化时更新 media_position，播放中要自己补上流逝的时间。"""
    position = num(attrs.get("media_position"))
    if position is None:
        return None
    updated = attrs.get("media_position_updated_at")
    if not updated:
        return position
    try:
        stamp = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return position + max(0.0, (datetime.now(timezone.utc) - stamp).total_seconds())
    except Exception:  # noqa: BLE001 - 解析不了就用原始值
        return position


def _select(entity_id, key, label, value, options, name, param, labels=None):
    labels = labels or {}
    return {
        "kind": "select",
        "key": key,
        "label": label,
        "value": value,
        "options": [{"value": v, "label": labels.get(v, v)} for v in options],
        **service(entity_id, name, **{param: "%value%"}),
    }


def _buttons(entity_id, key, items) -> dict:
    return {"kind": "buttons", "key": key, "items": items}


def _button(entity_id, label, name, primary=False, **params) -> dict:
    return {"label": label, "primary": primary, **service(entity_id, name, **params)}


# ---------------------------------------------------------------------------
# 各域构建器
# ---------------------------------------------------------------------------
def build_light(entity_id: str, state: str, attrs: dict) -> list[dict]:
    controls: list[dict] = []

    brightness = num(attrs.get("brightness"))
    modes = attrs.get("supported_color_modes") or []
    # 支持亮度才给亮度条：brightness 存在，或 color_modes 里有非 onoff 模式
    if brightness is not None or any(m != "onoff" for m in modes):
        pct = round(brightness / 255 * 100) if brightness is not None else 100
        controls.append(
            _slider(
                entity_id, "brightness", "亮度", max(1, pct), 1, 100, 1, "%",
                "turn_on", "brightness_pct",
                presets=[("10%", 10), ("50%", 50), ("100%", 100)],
            )
        )

    kelvin = num(attrs.get("color_temp_kelvin"))
    if kelvin is not None or "color_temp" in modes:
        low = int(num(attrs.get("min_color_temp_kelvin")) or 2000)
        high = int(num(attrs.get("max_color_temp_kelvin")) or 6500)
        value = int(kelvin) if kelvin else (low + high) // 2
        controls.append(
            _slider(
                entity_id, "color_temp", "色温", value, low, high, 50, "K",
                "turn_on", "color_temp_kelvin",
                presets=[("暖光", low), ("自然", (low + high) // 2), ("冷光", high)],
            )
        )

    effects = attrs.get("effect_list") or []
    if effects:
        controls.append(
            _select(entity_id, "effect", "灯效", attrs.get("effect"), effects, "turn_on", "effect")
        )

    # 支持彩色灯就加一排颜色（用 color_name，不需要前端做取色器）
    if COLOR_MODES & set(modes):
        controls.append(
            _select(
                entity_id, "color", "颜色", attrs.get("color_name"),
                list(COLOR_LABELS), "turn_on", "color_name", COLOR_LABELS,
            )
        )

    return controls


def build_cover(entity_id: str, state: str, attrs: dict) -> list[dict]:
    controls: list[dict] = []
    position = num(attrs.get("current_position"))
    features = int(num(attrs.get("supported_features")) or 0)

    # bit 4 = SET_POSITION
    if position is not None or features & 4:
        value = int(position) if position is not None else (100 if state == "open" else 0)
        controls.append(
            _slider(entity_id, "position", "开合度", value, 0, 100, 1, "%", "set_cover_position", "position")
        )

    # bit 128 = SET_TILT_POSITION
    tilt = num(attrs.get("current_tilt_position"))
    if tilt is not None or features & 128:
        controls.append(
            _slider(
                entity_id, "tilt", "叶片角度", int(tilt or 0), 0, 100, 1, "%",
                "set_cover_tilt_position", "tilt_position",
            )
        )

    controls.append(
        _buttons(
            entity_id,
            "cover",
            [
                _button(entity_id, "打开", "open_cover"),
                _button(entity_id, "暂停", "stop_cover"),
                _button(entity_id, "关闭", "close_cover"),
            ],
        )
    )
    return controls


def build_climate(entity_id: str, state: str, attrs: dict) -> list[dict]:
    controls: list[dict] = []
    # HA 的 climate 目标温度属性是 `temperature`；少数自定义集成仍用
    # `target_temperature`，两个都认。
    target = num(attrs.get("temperature"))
    if target is None:
        target = num(attrs.get("target_temperature"))
    low = num(attrs.get("min_temp")) or 7
    high = num(attrs.get("max_temp")) or 35
    step = num(attrs.get("target_temp_step")) or 1
    features = int(num(attrs.get("supported_features")) or 0)

    if target is not None:
        controls.append(
            {
                "kind": "stepper",
                "key": "temperature",
                "label": "目标温度",
                "value": target,
                "min": low,
                "max": high,
                "step": step,
                "unit": "°C",
                **service(entity_id, "set_temperature", temperature="%value%"),
            }
        )
        controls.append(
            _slider(entity_id, "temperature_slider", "", target, low, high, step, "", "set_temperature", "temperature")
        )
    else:
        # 范围控温（地暖、部分中央空调）：没有单一目标温度，改给上下限
        for key, label, param in (
            ("temp_low", "低温", "target_temp_low"),
            ("temp_high", "高温", "target_temp_high"),
        ):
            value = num(attrs.get(param))
            if value is None:
                continue
            controls.append(
                {
                    "kind": "stepper",
                    "key": key,
                    "label": label,
                    "value": value,
                    "min": low,
                    "max": high,
                    "step": step,
                    "unit": "°C",
                    **service(entity_id, "set_temperature", **{param: "%value%"}),
                }
            )

    hvac_modes = attrs.get("hvac_modes") or []
    if hvac_modes:
        controls.append(
            _select(
                entity_id, "hvac_mode", "模式",
                attrs.get("hvac_mode") or state, hvac_modes,
                "set_hvac_mode", "hvac_mode", HVAC_LABELS,
            )
        )

    fan_modes = attrs.get("fan_modes") or []
    if fan_modes:
        controls.append(
            _select(
                entity_id, "fan_mode", "风速",
                attrs.get("fan_mode"), fan_modes,
                "set_fan_mode", "fan_mode", FAN_LABELS,
            )
        )

    swing_modes = attrs.get("swing_modes") or []
    if swing_modes:
        controls.append(
            _select(
                entity_id, "swing_mode", "摆风",
                attrs.get("swing_mode"), swing_modes,
                "set_swing_mode", "swing_mode", SWING_LABELS,
            )
        )

    # 水平摆风是独立的一套模式（不是所有空调都有）
    swing_h = attrs.get("swing_horizontal_modes") or []
    if swing_h and (features & 128 or attrs.get("swing_horizontal_mode")):
        controls.append(
            _select(
                entity_id, "swing_horizontal_mode", "水平摆风",
                attrs.get("swing_horizontal_mode"), swing_h,
                "set_swing_horizontal_mode", "swing_horizontal_mode", SWING_LABELS,
            )
        )

    presets = attrs.get("preset_modes") or []
    if presets:
        controls.append(
            _select(
                entity_id, "preset_mode", "预设",
                attrs.get("preset_mode"), presets,
                "set_preset_mode", "preset_mode", PRESET_LABELS,
            )
        )

    humidity = num(attrs.get("humidity"))
    if humidity is not None:
        h_low = int(num(attrs.get("min_humidity")) or 30)
        h_high = int(num(attrs.get("max_humidity")) or 90)
        controls.append(
            _slider(
                entity_id, "humidity", "目标湿度", int(humidity), h_low, h_high, 1, "%",
                "set_humidity", "humidity",
            )
        )

    # bit 64 = AUX_HEAT，attr 是 "on"/"off"
    aux = attrs.get("aux_heat")
    if aux is not None or features & 64:
        controls.append(
            _switch(entity_id, "aux_heat", "辅热", aux == "on", "set_aux_heat", "aux_heat")
        )

    return controls


def build_fan(entity_id: str, state: str, attrs: dict) -> list[dict]:
    controls: list[dict] = []
    percentage = num(attrs.get("percentage"))
    if percentage is not None:
        controls.append(
            _slider(entity_id, "percentage", "风速", max(1, round(percentage)), 1, 100, 1, "%", "set_percentage", "percentage")
        )

    presets = attrs.get("preset_modes") or []
    if presets:
        controls.append(
            _select(
                entity_id, "preset_mode", "档位",
                attrs.get("preset_mode"), presets,
                "set_preset_mode", "preset_mode", PRESET_LABELS,
            )
        )

    direction = attrs.get("direction")
    if direction:
        controls.append(
            _select(
                entity_id, "direction", "风向", direction,
                ["forward", "reverse"], "set_direction", "direction",
                {"forward": "正转", "reverse": "反转"},
            )
        )

    # 摇头是布尔能力
    oscillating = attrs.get("oscillating")
    if oscillating is not None:
        controls.append(
            _switch(entity_id, "oscillating", "摇头", oscillating, "set_oscillating", "oscillating")
        )

    return controls


def build_media_player(entity_id: str, state: str, attrs: dict) -> list[dict]:
    playing = state == "playing"
    controls: list[dict] = [
        {
            "kind": "media",
            "key": "media",
            "title": attrs.get("media_title") or "",
            "artist": attrs.get("media_artist") or attrs.get("media_album_name") or "",
            "playing": playing,
            # 上一首/播放暂停/下一首也做成按钮列表，前端不必知道播放器的服务名
            "items": [
                _button(entity_id, "上一首", "media_previous_track"),
                _button(entity_id, "暂停" if playing else "播放", "media_play_pause", primary=True),
                _button(entity_id, "下一首", "media_next_track"),
            ],
        }
    ]

    duration = num(attrs.get("media_duration"))
    position = _elapsed(attrs)
    # bit 2 = SEEK；声明了 features 但没有 SEEK 的播放器不给进度条，免得点了报错
    features = int(num(attrs.get("supported_features")) or 0)
    if duration and position is not None and (not features or features & 2):
        controls.append(
            _slider(
                entity_id, "seek", "进度", int(min(position, duration)), 0, int(duration), 5, "",
                "media_seek", "seek_position", mmss=True, total=int(duration),
            )
        )

    volume = num(attrs.get("volume_level"))
    if volume is not None:
        controls.append(
            _slider(entity_id, "volume", "音量", round(volume * 100), 0, 100, 1, "%", "volume_set", "volume_level")
        )
        # 音量的服务参数是 0-1，用 ratio 占位符让前端换算
        controls[-1]["params"] = {"volume_level": "%ratio%"}

    sources = attrs.get("source_list") or []
    if sources:
        controls.append(
            _select(entity_id, "source", "输入源", attrs.get("source"), sources, "select_source", "source")
        )

    muted = attrs.get("is_volume_muted")
    if isinstance(muted, bool):
        controls.append(
            _switch(entity_id, "mute", "静音", muted, "volume_mute", "is_volume_muted")
        )

    shuffle = attrs.get("shuffle")
    if isinstance(shuffle, bool):
        controls.append(
            _switch(entity_id, "shuffle", "随机播放", shuffle, "shuffle_set", "shuffle")
        )

    repeat = attrs.get("repeat")
    if repeat in ("off", "music", "one"):
        controls.append(
            _select(
                entity_id,
                "repeat",
                "循环",
                repeat,
                ["off", "music", "one"],
                "repeat_set",
                "repeat",
                {"off": "关闭", "music": "列表循环", "one": "单曲循环"},
            )
        )

    sound_modes = attrs.get("sound_mode_list") or []
    if sound_modes:
        controls.append(
            _select(
                entity_id,
                "sound_mode",
                "音效模式",
                attrs.get("sound_mode"),
                sound_modes,
                "select_sound_mode",
                "sound_mode",
            )
        )

    return controls


def build_remote(entity_id: str, state: str, attrs: dict) -> list[dict]:
    """遥控器：活动选择 + 一条可手输的红外/网络命令。"""
    controls: list[dict] = []
    activities = attrs.get("activity_list") or []
    if activities:
        controls.append(
            _select(
                entity_id,
                "activity",
                "当前活动",
                attrs.get("current_activity"),
                activities,
                "turn_on",
                "activity",
            )
        )

    controls.append(
        {
            "kind": "input",
            "key": "command",
            "label": "发送命令",
            "value": "",
            "max": 64,
            "placeholder": "例如：KEY_POWER",
            **service(entity_id, "send_command", command="%value%"),
        }
    )
    return controls


def build_todo(entity_id: str, state: str, attrs: dict) -> list[dict]:
    """待办清单：允许快速添加一条，清单本身由详情页读取。"""
    if not int(attrs.get("supported_features") or 0) & 1:
        return []
    return [
        {
            "kind": "input",
            "key": "item",
            "label": "添加待办",
            "value": "",
            "max": 80,
            "placeholder": "例如：取快递",
            **service(entity_id, "add_item", item="%value%"),
        }
    ]


def build_vacuum(entity_id: str, state: str, attrs: dict) -> list[dict]:
    controls: list[dict] = []
    battery = num(attrs.get("battery_level"))
    if battery is not None:
        controls.append(
            {"kind": "value", "key": "battery", "label": "电量", "value": int(battery), "unit": "%"}
        )

    speeds = attrs.get("fan_speed_list") or []
    if speeds:
        controls.append(
            _select(entity_id, "fan_speed", "吸力", attrs.get("fan_speed"), speeds, "set_fan_speed", "fan_speed")
        )

    controls.append(
        _buttons(
            entity_id,
            "vacuum",
            [
                _button(entity_id, "开始清扫", "start", primary=True),
                _button(entity_id, "暂停", "pause"),
                _button(entity_id, "定点清扫", "clean_spot"),
                _button(entity_id, "回充", "return_to_base"),
                _button(entity_id, "定位寻机", "locate"),
            ],
        )
    )
    return controls


def build_lock(entity_id: str, state: str, attrs: dict) -> list[dict]:
    locked = state == "locked"
    return [
        _buttons(
            entity_id,
            "lock",
            [
                _button(entity_id, "上锁", "lock", primary=not locked),
                _button(entity_id, "解锁", "unlock", primary=locked),
            ],
        )
    ]


def build_text(entity_id: str, state: str, attrs: dict) -> list[dict]:
    return [
        {
            "kind": "input",
            "key": "value",
            "label": "内容",
            "value": "" if state in ("unknown", "unavailable") else state,
            "max": int(num(attrs.get("max")) or 255),
            "placeholder": "输入要写入的内容",
            **service(entity_id, "set_value", value="%value%"),
        }
    ]


def build_number(entity_id: str, state: str, attrs: dict) -> list[dict]:
    """number / input_number：可调数值。"""
    value = num(state)
    if value is None:
        return []
    return [
        _slider(
            entity_id, "value", "数值", value,
            num(attrs.get("min")) or 0,
            num(attrs.get("max")) or 100,
            num(attrs.get("step")) or 1,
            attrs.get("unit_of_measurement") or "",
            "set_value", "value",
        )
    ]


def build_select(entity_id: str, state: str, attrs: dict) -> list[dict]:
    """select / input_select：枚举。"""
    options = attrs.get("options") or []
    if not options:
        return []
    return [_select(entity_id, "option", "选项", state, options, "select_option", "option")]


def build_valve(entity_id: str, state: str, attrs: dict) -> list[dict]:
    return [
        _buttons(
            entity_id,
            "valve",
            [
                _button(entity_id, "打开", "open_valve", primary=True),
                _button(entity_id, "关闭", "close_valve"),
            ],
        )
    ]


def build_water_heater(entity_id: str, state: str, attrs: dict) -> list[dict]:
    controls: list[dict] = []
    target = num(attrs.get("temperature"))
    if target is not None:
        controls.append(
            {
                "kind": "stepper",
                "key": "temperature",
                "label": "目标温度",
                "value": target,
                "min": num(attrs.get("min_temp")) or 30,
                "max": num(attrs.get("max_temp")) or 70,
                "step": 1,
                "unit": "°C",
                **service(entity_id, "set_temperature", temperature="%value%"),
            }
        )
    modes = attrs.get("operation_list") or []
    if modes:
        controls.append(
            _select(entity_id, "operation_mode", "模式", attrs.get("operation_mode"), modes, "set_operation_mode", "operation_mode")
        )
    return controls


def build_humidifier(entity_id: str, state: str, attrs: dict) -> list[dict]:
    controls: list[dict] = []
    # HA 的 humidifier 目标湿度属性是 `humidity`
    target = num(attrs.get("humidity"))
    if target is None:
        target = num(attrs.get("target_humidity"))
    if target is not None:
        controls.append(
            _slider(entity_id, "humidity", "目标湿度", round(target), 1, 100, 1, "%", "set_humidity", "humidity")
        )
    modes = attrs.get("available_modes") or []
    if modes:
        controls.append(
            _select(entity_id, "mode", "模式", attrs.get("mode"), modes, "set_mode", "mode")
        )
    return controls


def build_alarm(entity_id: str, state: str, attrs: dict) -> list[dict]:
    """报警面板：布防 / 外出布防 / 夜间布防 / 撤防。

    需要密码的面板（`code_format` 非空）不出控件：小程序侧没法验证密码，
    给了按钮也只会换来一条「密码错误」。只显示状态，让人回 HA 里操作。
    """
    if attrs.get("code_format"):
        return []

    features = int(num(attrs.get("supported_features")) or 0)
    items = [_button(entity_id, "在家布防", "alarm_arm_home", primary=state == "disarmed")]
    items.append(_button(entity_id, "外出布防", "alarm_arm_away"))
    # ARM_NIGHT = 32；老面板没有这个 feature 时不显示夜间布防
    if features & 32:
        items.append(_button(entity_id, "夜间布防", "alarm_arm_night"))
    items.append(_button(entity_id, "撤防", "alarm_disarm"))
    return [_buttons(entity_id, "alarm", items)]


def build_timer(entity_id: str, state: str, attrs: dict) -> list[dict]:
    """timer：启动 / 暂停 / 取消 / 结束，外加剩余时间。"""
    controls: list[dict] = []
    remaining = attrs.get("remaining")
    if remaining:
        controls.append(
            {"kind": "value", "key": "remaining", "label": "剩余", "value": remaining, "unit": ""}
        )
    controls.append(
        _buttons(
            entity_id,
            "timer",
            [
                _button(entity_id, "启动", "start", primary=state != "active"),
                _button(entity_id, "暂停", "pause"),
                _button(entity_id, "取消", "cancel"),
                _button(entity_id, "结束", "finish"),
            ],
        )
    )
    return controls


def build_counter(entity_id: str, state: str, attrs: dict) -> list[dict]:
    """counter：加减和清零，步长来自配置。"""
    controls: list[dict] = []
    value = num(state)
    step = int(num(attrs.get("step")) or 1)
    if value is not None:
        controls.append(
            {"kind": "value", "key": "count", "label": "当前值", "value": int(value), "unit": ""}
        )
    controls.append(
        _buttons(
            entity_id,
            "counter",
            [
                _button(entity_id, f"+{step}", "increment", primary=True),
                _button(entity_id, f"-{step}", "decrement"),
                _button(entity_id, "清零", "reset"),
            ],
        )
    )
    return controls


def _datetime_input(entity_id, label, service_name, param, value, placeholder, picker):
    """picker 决定前端用哪种原生选择器：date / time / datetime。"""
    return [
        {
            "kind": "input",
            "key": "value",
            "label": label,
            "value": "" if value in (None, "unknown", "unavailable") else value,
            "max": 32,
            "placeholder": placeholder,
            "picker": picker,
            **service(entity_id, service_name, **{param: "%value%"}),
        }
    ]


def build_input_datetime(entity_id: str, state: str, attrs: dict) -> list[dict]:
    """input_datetime：按 has_date / has_time 决定写日期、写时间还是两者。"""
    has_date = attrs.get("has_date", True)
    has_time = attrs.get("has_time", True)
    if has_date and has_time:
        return _datetime_input(
            entity_id, "日期时间", "set_datetime", "datetime", state, "2026-11-17 18:30:00", "datetime"
        )
    if has_date:
        return _datetime_input(entity_id, "日期", "set_datetime", "date", state, "2026-11-17", "date")
    return _datetime_input(entity_id, "时间", "set_datetime", "time", state, "18:30:00", "time")


def build_date(entity_id: str, state: str, attrs: dict) -> list[dict]:
    return _datetime_input(entity_id, "日期", "set_value", "date", state, "2026-11-17", "date")


def build_time(entity_id: str, state: str, attrs: dict) -> list[dict]:
    return _datetime_input(entity_id, "时间", "set_value", "time", state, "18:30:00", "time")


def build_datetime(entity_id: str, state: str, attrs: dict) -> list[dict]:
    return _datetime_input(entity_id, "日期时间", "set_value", "datetime", state, "2026-11-17 18:30:00", "datetime")


def build_mower(entity_id: str, state: str, attrs: dict) -> list[dict]:
    """lawn_mower：和扫地机同一套动作。"""
    return [
        _buttons(
            entity_id,
            "mower",
            [
                _button(entity_id, "开始割草", "start_mowing", primary=True),
                _button(entity_id, "暂停", "pause"),
                _button(entity_id, "回充", "dock"),
            ],
        )
    ]


def build_update(entity_id: str, state: str, attrs: dict) -> list[dict]:
    """update：有新版才给安装按钮，安装中不给（避免重复点）。"""
    controls: list[dict] = []
    latest = attrs.get("latest_version")
    installed = attrs.get("installed_version")
    if latest and latest != installed:
        controls.append(
            {"kind": "value", "key": "latest", "label": "新版本", "value": latest, "unit": ""}
        )
    in_progress = bool(attrs.get("in_progress"))
    if state == "on" and not in_progress:
        controls.append(
            _buttons(entity_id, "update", [_button(entity_id, "安装更新", "install", primary=True)])
        )
    elif in_progress:
        controls.append(
            {"kind": "value", "key": "progress", "label": "状态", "value": "正在安装", "unit": ""}
        )
    return controls


def build_siren(entity_id: str, state: str, attrs: dict) -> list[dict]:
    """siren：音调只在响铃时可选，避免“选音调=直接开始响”。"""
    tones = attrs.get("available_tones") or []
    if not tones or state not in ON_STATES:
        return []
    return [_select(entity_id, "tone", "音调", attrs.get("tone"), tones, "turn_on", "tone")]


# 域 → 构建器。新增实体域只需在这里加一行。
BUILDERS = {
    "light": build_light,
    "cover": build_cover,
    "climate": build_climate,
    "fan": build_fan,
    "media_player": build_media_player,
    "remote": build_remote,
    "todo": build_todo,
    "vacuum": build_vacuum,
    "lock": build_lock,
    "text": build_text,
    "input_text": build_text,
    "number": build_number,
    "input_number": build_number,
    "select": build_select,
    "input_select": build_select,
    "valve": build_valve,
    "water_heater": build_water_heater,
    "humidifier": build_humidifier,
    "alarm_control_panel": build_alarm,
    "timer": build_timer,
    "counter": build_counter,
    "input_datetime": build_input_datetime,
    "date": build_date,
    "time": build_time,
    "datetime": build_datetime,
    "lawn_mower": build_mower,
    "update": build_update,
    "siren": build_siren,
}


def state_label(domain: str, state: str):
    """HA 翻译表里拿不到的状态名，从这里补中文；没有就返回 None。"""
    return STATE_LABELS.get(domain, {}).get(state)


def build_generic(entity_id: str, state: str, attrs: dict) -> list[dict]:
    """未知域兜底：按常见属性推导控件。

    自定义集成千奇百怪，但它们往往沿用 HA 的通用属性名。只要有
    percentage / brightness / position / temperature 这类字段，
    就能自动得到一个能用的滑条，不需要为每个集成写代码。
    """
    controls: list[dict] = []
    domain = domain_of(entity_id)

    # 百分比类 → 滑条
    mapping = (
        ("percentage", "开合度", "set_percentage", "percentage", 1),
        ("brightness", "亮度", "turn_on", "brightness_pct", 1),
        ("position", "位置", "set_cover_position", "position", 0),
        ("volume_level", "音量", "volume_set", "volume_level", 0),
        ("target_humidity", "目标湿度", "set_humidity", "humidity", 1),
    )
    for key, label, name, param, low in mapping:
        raw = num(attrs.get(key))
        if raw is None:
            continue
        if key == "brightness":
            value = max(1, round(raw / 255 * 100))
        elif key == "volume_level":
            value = round(raw * 100)
        else:
            value = max(low, round(raw))
        controls.append(
            _slider(entity_id, key, label, value, low, 100, 1, "%", name, param)
        )

    # 温度类（传感器不当作可控）
    target = num(attrs.get("target_temperature")) or num(attrs.get("temperature"))
    if target is not None and domain not in READ_ONLY_DOMAINS:
        controls.append(
            {
                "kind": "stepper",
                "key": "target_temperature",
                "label": "目标温度",
                "value": target,
                "min": num(attrs.get("min_temp")) or 7,
                "max": num(attrs.get("max_temp")) or 35,
                "step": 1,
                "unit": "°C",
                **service(entity_id, "set_temperature", temperature="%value%"),
            }
        )

    # 枚举类
    for modes_key, label, setter in (
        ("hvac_modes", "模式", "set_hvac_mode"),
        ("preset_modes", "档位", "set_preset_mode"),
        ("operation_list", "模式", "set_operation_mode"),
    ):
        values = attrs.get(modes_key)
        if not values or not isinstance(values, list):
            continue
        param = setter[len("set_"):]
        current_key = modes_key.replace("_modes", "").replace("_list", "")
        controls.append(
            _select(
                entity_id, param, label, attrs.get(current_key), values,
                setter, param, PRESET_LABELS,
            )
        )

    return controls


def build_controls(entity_id: str, state: str, attrs: dict) -> list[dict]:
    """统一入口：先给电源/动作，再叠加域专属控件。"""
    domain = domain_of(entity_id)
    controls: list[dict] = []

    if domain in TOGGLE_DOMAINS:
        on = state in ON_STATES
        controls.append(
            {
                "kind": "power",
                "key": "power",
                "label": "开关",
                "on": on,
                **service(entity_id, "turn_off" if on else "turn_on"),
            }
        )

    if domain in ACTION_DOMAINS:
        controls.append(
            {
                "kind": "action",
                "key": "action",
                "label": "执行",
                **service(entity_id, ACTION_SERVICES[domain]),
            }
        )

    builder = BUILDERS.get(domain)
    if builder is not None:
        controls.extend(builder(entity_id, state, attrs))
    elif domain not in READ_ONLY_DOMAINS:
        controls.extend(build_generic(entity_id, state, attrs))

    return controls


COLOR_MODE_LABELS = {
    "onoff": "仅开关",
    "brightness": "亮度",
    "color_temp": "色温",
    "hs": "彩色",
    "xy": "彩色",
    "rgb": "彩色",
    "rgbw": "彩色",
    "rgbww": "彩色",
    "white": "白光",
}

MODE_LIST_LABELS = {
    "hvac_modes": HVAC_LABELS,
    "fan_modes": FAN_LABELS,
    "swing_modes": SWING_LABELS,
    "swing_horizontal_modes": SWING_LABELS,
    "preset_modes": PRESET_LABELS,
}


def summarize(
    attrs: dict,
    limit: int = 12,
    exclude: frozenset | set | None = None,
    translate=None,
    localize: bool = True,
) -> list[dict]:
    """属性摘要，给「设备信息」和未知域展示用。

    localize=False 时只返回原始 key/value，中文名和状态词由小程序前端翻译；
    localize=True 保留旧行为，供独立调用和兼容测试使用。
    """
    rows: list[dict] = []
    excluded = exclude or ()

    def translated(key: str, value: str) -> str:
        if translate is None:
            return value
        try:
            result = translate(key, value)
            return str(result) if result is not None else value
        except Exception:  # noqa: BLE001 - 翻译失败不能影响详情页
            return value

    def raw_value(value) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, float):
            return str(round(value, 2))
        return str(value)

    for key, value in attrs.items():
        if key in HIDDEN_ATTRS or key in excluded or value is None or value == "":
            continue
        if isinstance(value, (dict, list, tuple, set)):
            if not value:
                continue
            if isinstance(value, (list, tuple, set)):
                if localize:
                    items = [translated(key, str(item)) for item in value]
                    names = MODE_LIST_LABELS.get(key)
                    if names and all(item == str(raw) for item, raw in zip(items, value)):
                        items = [names.get(str(item), str(item)) for item in value]
                    value = "、".join(items)[:80]
                else:
                    value = "、".join(raw_value(item) for item in value)[:80]
            else:
                value = str(value)[:80]
        elif localize:
            raw_text = "true" if value is True else "false" if value is False else str(value)
            localized = translated(key, raw_text)
            if localized != raw_text:
                value = localized
            elif isinstance(value, bool):
                value = "开" if value else "关"
            elif isinstance(value, float):
                value = round(value, 2)
            elif key == "color_mode":
                value = COLOR_MODE_LABELS.get(str(value), str(value))
            elif key == "hvac_action":
                value = HVAC_ACTION_LABELS.get(str(value), str(value))
            elif isinstance(value, str) and value in ("on", "off") and key not in {"color_mode"} and key not in FREE_TEXT_ATTRS:
                value = "开" if value == "on" else "关"
        else:
            value = raw_value(value)

        label = ATTR_LABELS.get(key, key) if localize else key
        rows.append({"key": key, "label": label, "value": str(value)})
        if len(rows) >= limit:
            break
    return rows


def card_flags(
    entity_id: str,
    state: str,
    attrs: dict,
    time_zone: str | None = None,
) -> dict:
    """卡片需要的少量字段：开关 / 动作 / 数值。"""
    domain = domain_of(entity_id)
    flags: dict = {"toggle": None, "action": None, "value": None, "unit": None}

    if domain in TOGGLE_DOMAINS:
        on = state in ON_STATES
        flags["toggle"] = {
            "on": on,
            "service": f"{domain}.{'turn_off' if on else 'turn_on'}",
            "entity_id": entity_id,
            "params": {},
        }

    if domain in ACTION_DOMAINS:
        flags["action"] = service(entity_id, ACTION_SERVICES[domain])

    temporal = domain in TEMPORAL_DOMAINS or attrs.get("device_class") in TEMPORAL_CLASSES
    if temporal:
        text = format_temporal(
            state,
            domain,
            attrs.get("device_class") or "",
            time_zone=time_zone,
            has_date=attrs.get("has_date"),
            has_time=attrs.get("has_time"),
        )
        if text:
            flags["value"] = text
            flags["unit"] = ""
    elif domain in VALUE_DOMAINS:
        unit = attrs.get("unit_of_measurement") or ""
        numeric = num(state)
        if numeric is not None:
            flags["value"] = round(numeric, 2) if isinstance(numeric, float) else numeric
            flags["unit"] = unit
        elif state not in ("unknown", "unavailable"):
            flags["value"] = state
            flags["unit"] = unit

    # 待办数量、下一条日程、事件类型和当前天气都适合作为卡片主信息。
    if domain == "todo":
        numeric = num(state)
        if numeric is not None:
            flags["value"] = int(numeric)
            flags["unit"] = "项"
    elif domain == "calendar":
        message = str(attrs.get("message") or "").strip()
        if message:
            flags["value"] = message
            flags["unit"] = ""
        elif attrs.get("start_time"):
            flags["value"] = format_temporal(
                attrs.get("start_time"), "datetime", time_zone=time_zone
            )
            flags["unit"] = ""
    elif domain == "event":
        kind = str(attrs.get("event_type") or "").strip()
        if kind:
            flags["value"] = kind
            flags["unit"] = ""
    elif domain == "weather":
        temperature = num(attrs.get("temperature"))
        if temperature is not None:
            flags["value"] = round(temperature, 1)
            flags["unit"] = attrs.get("temperature_unit") or "°C"

    # 卡片上没有数值的域，但有个更有用的短信息（比如定时器剩余时间）
    if domain == "timer" and flags["value"] is None:
        remaining = attrs.get("remaining")
        if remaining:
            flags["value"] = remaining
            flags["unit"] = ""

    return flags
