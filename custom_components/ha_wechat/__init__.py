from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CoreState, HomeAssistant, Context, split_entity_id
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.network import get_url
from homeassistant.const import __version__ as current_version
from homeassistant.const import (
    EVENT_HOMEASSISTANT_STARTED,
    EVENT_STATE_CHANGED,
)
import paho.mqtt.client as mqtt
import logging, json, time, uuid, aiohttp, urllib

from .util import async_generate_qrcode
from .EncryptHelper import EncryptHelper
from .manifest import manifest
from .const import CONVERSATION_ASSISTANT

_LOGGER = logging.getLogger(__name__)
DOMAIN = manifest.domain

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    config = entry.data
    topic = config['topic']
    key = config['key']
    hass.data[DOMAIN] = await hass.async_add_executor_job(HaMqtt, hass, {
        'topic': topic,
        'key': key
    })

    async def qrcode_service(service):
        await async_generate_qrcode(hass, topic, key)

    hass.services.async_register(DOMAIN, 'qrcode', qrcode_service)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data[DOMAIN].unload()
    del hass.data[DOMAIN]
    return True

class HaMqtt():

    def __init__(self, hass, config):
        self.hass = hass
        self.topic = config.get('topic')
        self.key = config.get('key')
        self.msg_cache = {}
        self.is_connected = False

        if hass.state == CoreState.running:
            self.connect()
        else:
            hass.bus.listen_once(EVENT_HOMEASSISTANT_STARTED, self.connect)

    @property
    def encryptor(self):
        return EncryptHelper(self.key, time.strftime('%Y-%m-%d', time.localtime()))

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
        self.client.subscribe(self.topic, 2)
        self.is_connected = True

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
                print('【ha-mqtt】消息已过期')
                return

            msg_id = data['id']
            # 判断消息是否已接收
            if msg_id in self.msg_cache:
                print('【ha-mqtt】消息已处理')
                return

            # 设置消息为已接收
            self.msg_cache[msg_id] = now

            # 消息处理
            self.hass.async_create_task(self.async_handle_message(data))

        except Exception as ex:
            print(ex)

    def on_subscribe(self, client, userdata, mid, granted_qos):
        print("【ha_mqtt】On Subscribed: qos = %d" % granted_qos)

    def on_disconnect(self, client, userdata, rc):
        print("【ha_mqtt】Unexpected disconnection %s" % rc)
        self.is_connected = False

    def publish(self, topic, data):
        # 判断当前连接状态
        if self.client._state == 2:
            _LOGGER.debug('断开重连')
            self.client.reconnect()
            self.client.loop_start()

        # 加密消息
        payload = self.encryptor.Encrypt(json.dumps(data))
        self.client.publish(topic, payload, qos=1)

    async def async_handle_message(self, data):
        msg_id = data['id']
        msg_topic = data['topic']
        msg_type = data['type']
        msg_data = data['data']
        
        body = msg_data.get('data', {})
        print(data)
        result = None

        if msg_type == 'join':
            self.hass.async_create_task(self.hass.services.async_call('persistent_notification', 'create', {
                'title': '微信控制',
                'message': f'{msg_data.get("name")}加入成功'
            }))
            result = {
                'ha_version': current_version,
                'version': manifest.version
            }
        elif msg_type == '/api/states':
            states = self.hass.states.async_all(body)
            def states_all(state):
                attrs = state.attributes
                return {
                    'id': state.entity_id,
                    'name': attrs.get('friendly_name'),
                    'icon': attrs.get('icon'),
                    'state': state.state
                }
            result = list(map(states_all, states))
        elif msg_type == '/api/domains':
            states = self.hass.states.async_all()
            def states_all(state):
                domain = split_entity_id(state.entity_id)[0]
                return domain
            result = list(set(map(states_all, states)))
        elif msg_type == '/api/states/entity_id':
            state = self.hass.states.get(body)
            attrs = state.attributes
            result = {
                'id': state.entity_id,
                'name': attrs.get('friendly_name'),
                'icon': attrs.get('icon'),
                'state': state.state
            }
        elif msg_type == 'conversation':
            conversation = self.hass.data.get(CONVERSATION_ASSISTANT)
            plain = '请安装最新版语音助手'
            if conversation is not None:
                text = msg_data['text']
                res = await conversation.recognize(text)
                intent_result = res.response
                # 推送回复消息
                plain = intent_result.speech['plain']
            result = {
                'text': plain
            }

        if result is not None:
            self.publish(msg_topic, {
                'id': msg_id,
                'time': int(time.time()),
                'type': msg_type,
                'data': result
            })