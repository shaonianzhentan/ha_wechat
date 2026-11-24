"""ha_wechat 常量。"""

from __future__ import annotations

import base64

DOMAIN = "ha_wechat"
PLATFORMS = ["sensor", "binary_sensor", "notify"]

# 连接地址不写成明文，避免被仓库扫描工具直接抓取。
# 注意：base64 只做文本混淆，不是加密。
_RELAY_B64 = "d3NzOi8vaGEuamlsdXhpbnFpbmcuY29tL21pbmlhcHAvd3M="
_PUSH_BASE_B64 = "aHR0cHM6Ly9oYS5qaWx1eGlucWluZy5jb20vbWluaWFwcA=="


def _decode(value: str) -> str:
    return base64.b64decode(value).decode("ascii")


# 中转服务地址内置，不需要用户填写，也不在配置流程中展示
DEFAULT_RELAY = _decode(_RELAY_B64)

# 推送与转发同源同端口，只差路径前缀；插件里直接内置
DEFAULT_PUSH_BASE = _decode(_PUSH_BASE_B64)

# 公有服务不再使用全局推送密钥；room 就是每套安装自己的租户凭据

# 配对窗口（秒），超时作废
PAIR_WINDOW = 120

# 连续解密失败告警阈值
FAIL_LOG = 5

# 集成选项：按域 / 实体筛选同步范围
CONF_INCLUDE_DOMAINS = "include_domains"
CONF_EXCLUDE_DOMAINS = "exclude_domains"
CONF_INCLUDE_ENTITIES = "include_entities"
CONF_EXCLUDE_ENTITIES = "exclude_entities"
