from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CoreState, HomeAssistant
import homeassistant.helpers.config_validation as cv

from homeassistant.const import (
    EVENT_HOMEASSISTANT_STARTED,
    EVENT_STATE_CHANGED,
)

import paho.mqtt.client as mqtt
import logging, json, time, uuid

from .EncryptHelper import EncryptHelper
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
CONFIG_SCHEMA = cv.deprecated(DOMAIN)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    wx = Wechat(hass)
    await wx.init()
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return True

class Wechat():

    def __init__(self, hass):
        self.hass = hass
        if hass.state == CoreState.running:
            self.connect()
        else:
            hass.bus.listen_once(EVENT_HOMEASSISTANT_STARTED, self.connect)

    # 初始化
    async def init(self):        
        # 检测是否安装语音小助手
        pass
        # 判断是否生成配置

    # 生成二维码
    async def generate_qrcode(self):
        key = str(uuid.uuid1())
        iv = 'homeassistant'
        self.encryptor = EncryptHelper(key, iv)
        # 调用生成二维码接口
        uid = self.encryptor.md5(key + iv)
        self.uid = uid
        self.event_data = {
            'uid': uid,
            'data': { 'key': key, 'iv': iv }
        }

        # 发送扫码通知

        # 监听事件回调

        # 清除并保存key

    def connect(self, event=None):
        HOST = 'broker-cn.emqx.io'
        PORT = 1883
        client = mqtt.Client()        
        self.client = client
        client.on_connect = self.on_connect
        client.on_message = self.on_message
        client.on_subscribe = self.on_subscribe
        client.on_disconnect = self.on_disconnect
        client.connect(HOST, PORT, 60)
        client.loop_start()

    def on_connect(self, client, userdata, flags, rc):
        print('connectd')
        self.client.subscribe(self.uid, 2)

    def on_message(self, client, userdata, msg):
        payload = str(msg.payload.decode('utf-8'))
        # 解析消息

        # 判断消息是否过期

        # 判断消息是否已接收

        # 接收推送信息

        # 设置消息为已接收

        # 调用语音小助手API

        # 推送回复消息

    def on_subscribe(self, client, userdata, mid, granted_qos):
        print("On Subscribed: qos = %d" % granted_qos)

    def on_disconnect(self, client, userdata, rc):
        print("Unexpected disconnection %s" % rc)

    def publish(self, data):
        # 判断当前连接状态
        if self.client._state == 2:
            _LOGGER.debug('断开重连')
            self.client.reconnect()
            self.client.loop_start()

        now = time.time()
        # 加密消息
        payload = self.encryptor.Encrypt(json.dumps({
            'id': f'{now}',
            'time': int(now),
            'data': data
        }))
        self.client.publish(f"ha_wechat/{self.uid}", payload, qos=0)