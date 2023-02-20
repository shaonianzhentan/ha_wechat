from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CoreState, HomeAssistant, Context
import homeassistant.helpers.config_validation as cv
import re, urllib

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
    config = entry.data
    wx = await hass.async_add_executor_job(
        Wechat,
        hass,
        config
    )
    uid = config.get('uid')
    hass.data[DOMAIN + uid] = wx
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    config = entry.data
    uid = config.get('uid')
    topic = config.get('topic')

    key = DOMAIN + uid
    hass.data[key].unload()
    del hass.data[key]

    qrc = urllib.parse.quote(f'ha:{uid}#{topic}')
    await hass.services.async_call('persistent_notification', 'create', {
                'title': '使用【HomeAssistant家庭助理】小程序扫码关联',
                'message': f'[![qrcode](https://cdn.dotmaui.com/qrc/?t={qrc})](https://github.com/shaonianzhentan/ha_wechat) <font size="6">内含密钥和订阅主题<br/>请勿截图分享</font>'
            })
    return True

class Wechat():

    def __init__(self, hass, config):
        self.hass = hass
        self.uid = config.get('uid')
        self.topic = config.get('topic')
        self.msg_cache = {}
        self.encryptor = EncryptHelper(self.uid, 'ha-wechat')

        if hass.state == CoreState.running:
            self.connect()
        else:
            hass.bus.listen_once(EVENT_HOMEASSISTANT_STARTED, self.connect)

    def connect(self, event=None):
        HOST = 'test.mosquitto.org'
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
        print('【ha_wechat】connectd', self.topic)
        self.client.subscribe(self.topic, 2)

    def unload(self):
        self.client.disconnect()

    # 清理缓存消息
    def clear_cache_msg(self):
        now = int(time.time())
        for key in list(self.msg_cache.keys()):
            # 缓存消息超过10秒
            if key in self.msg_cache and now - 10 > self.msg_cache[key]:
                del self.msg_cache[key]

    def on_message(self, client, userdata, msg):
        payload = str(msg.payload.decode('utf-8'))
        try:
            # 解析消息
            data = json.loads(self.encryptor.Decrypt(payload))
            _LOGGER.debug(data)
            self.clear_cache_msg()

            now = int(time.time())
            # 判断消息是否过期(5s)
            if now - 5 > data['time']:
                print('【ha_wechat】消息已过期')
                return

            msg_id = data['id']
            # 判断消息是否已接收
            if msg_id in self.msg_cache:
                print('【ha_wechat】消息已处理')
                return

            # 设置消息为已接收
            self.msg_cache[msg_id] = now

            text = data['text']

            compileX = re.compile("^微信(图片|视频)(((ht|f)tps?):\/\/([\w\-]+(\.[\w\-]+)*\/)*[\w\-]+(\.[\w\-]+)*\/?(\?([\w\-\.,@?^=%&:\/~\+#]*)+)?)")
            findX = compileX.findall(text)
            if len(findX) > 0:
                mc = findX[0]
                self.hass.bus.fire('ha_wechat', {
                    'type': 'image' if mc[0] == '图片' else 'video',
                    'url': mc[1]
                })
                return

            # 调用语音小助手API
            self.hass.loop.create_task(self.async_process(text, conversation_id=msg_id))

        except Exception as ex:
            print(ex)

    async def async_process(self, text, conversation_id):
        conversation = self.hass.data.get('conversation_voice')
        plain = '请安装最新版语音助手'
        if conversation is not None:
            result = await conversation.recognize(text, conversation_id)
            intent_result = result.response
            # 推送回复消息
            plain = intent_result.speech['plain']

        topic = f'ha_wechat/{conversation_id}'
        _LOGGER.debug(topic)
        _LOGGER.debug(plain)
        await self.hass.async_add_executor_job(self.publish, topic, plain)

    def on_subscribe(self, client, userdata, mid, granted_qos):
        print("【ha_wechat】On Subscribed: qos = %d" % granted_qos)

    def on_disconnect(self, client, userdata, rc):
        print("【ha_wechat】Unexpected disconnection %s" % rc)

    def publish(self, topic, data):
        # 判断当前连接状态
        if self.client._state == 2:
            _LOGGER.debug('断开重连')
            self.client.reconnect()
            self.client.loop_start()

        # 加密消息
        payload = self.encryptor.Encrypt(json.dumps(data))
        self.client.publish(topic, payload, qos=0)