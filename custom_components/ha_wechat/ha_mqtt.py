import paho.mqtt.client as mqtt
import json
import time
import logging

from homeassistant.core import CoreState
from homeassistant.const import (
    EVENT_HOMEASSISTANT_STARTED
)
from homeassistant.helpers.network import get_url

from .EncryptHelper import EncryptHelper
from .assist import async_assistant
from .state_changed import StateChangedHandler
from .entity import EntityHelper

from .manifest import manifest

_LOGGER = logging.getLogger(__name__)

class HaMqtt():

    def __init__(self, hass, config):
        self.hass = hass
        self.topic = config.get('topic')
        self.key = config.get('key')
        self.entities = config.get('entities', [])
        self.msg_cache = {}
        self.msg_time = None
        self.receive_time = None
        self.is_connected = False
        self.client = None

        if hass.state == CoreState.running:
            self.connect()
        else:
            hass.bus.listen_once(EVENT_HOMEASSISTANT_STARTED, self.connect)

         # 初始化状态变化处理器
        self.state_handler = StateChangedHandler(hass, self)
        self.entity_helper = EntityHelper(hass)

    @property
    def encryptor(self):
        return EncryptHelper(self.key, time.strftime('%Y-%m-%d', time.localtime()))

    def connect(self, event=None):
        HOST = 'jiluxinqing.com'
        PORT = 1883
        client = mqtt.Client()
        self.client = client
        client.username_pw_set('wxapp', 'wxapp')
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

    def update_entities(self, entities):
        self.entities = entities

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
            
            self.msg_time = time.strftime(
                '%Y-%m-%d %H:%M:%S', time.localtime())

            now = int(time.time())
            self.receive_time = now
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
            self.hass.create_task(self.async_handle_message(data))

        except Exception as ex:
            print(ex)

    def on_subscribe(self, client, userdata, mid, granted_qos):
        print("【ha_mqtt】On Subscribed: qos = %d" % granted_qos)

    def on_disconnect(self, client, userdata, rc):
        print("【ha_mqtt】Unexpected disconnection %s" % rc)
        self.is_connected = False

    def publish(self, topic, data):
        if self.client is None:
            return
        # 判断当前连接状态
        if self.client._state == 2:
            _LOGGER.debug('断开重连')
            self.client.reconnect()
            self.client.loop_start()

        # 加密消息
        payload = self.encryptor.Encrypt(json.dumps(data))
        self.client.publish(topic, payload, qos=2)

    async def async_handle_data(self, data):
        ''' 数据处理 '''
        _LOGGER.debug(data)
        hass = self.hass
        result = None
        msg_type = data['type']
        msg_data = data['data']

        body = msg_data.get('data', {})

        if msg_type == 'join':
            # 加入
            states = []
            for entity_id in self.entities:
                state = await self.get_state(entity_id)
                if state is not None:
                    states.append(state)
            result = {                
                "name": self.hass.config.location_name,
                "host": get_url(self.hass),
                "version": manifest.version,
                "entities": states
            }
        elif msg_type == 'map':
            # 地图实体
            all_states = self.hass.states.async_all()
            entities = [
                await self.get_state(state.entity_id)
                for state in all_states
                if state.entity_id.split('.')[0] in ['device_tracker', 'person', 'zone']
                and 'latitude' in state.attributes
                and 'longitude' in state.attributes
            ]
            result = {
                "entities": entities
            }
        elif msg_type == 'call_service':
            # 调用服务
            service = msg_data.get('service')
            arr = service.split('.')
            await self.hass.services.async_call(arr[0], arr[1], body)
            result = {}
        elif msg_type == 'conversation':
            # 对话
            result = '未检测到语音助手功能'
            res = await async_assistant(hass, body)
            if res is not None:
                result = res

        return result

    async def async_handle_message(self, data):
        msg_id = data['id']
        msg_topic = data['topic']
        msg_type = data['type']

        result = await self.async_handle_data(data)

        if result is not None:
            self.publish(msg_topic, {
                'id': msg_id,
                'time': int(time.time()),
                'type': msg_type,
                'data': result
            })

    async def get_state(self, entity_id):
        ''' 获取实体状态 '''
        return await self.entity_helper.get_entity_info(entity_id)
