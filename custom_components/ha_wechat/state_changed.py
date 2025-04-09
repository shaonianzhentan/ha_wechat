import asyncio
import time
from homeassistant.const import EVENT_STATE_CHANGED

class StateChangedHandler:
    def __init__(self, hass, ha_mqtt):
        self.hass = hass
        self.ha_mqtt = ha_mqtt
        self.state_changes = {}  # 用于存储状态变化的实体，键为 entity_id
        self.lock = asyncio.Lock()  # 异步锁，防止并发问题

        # 监听实体状态变化
        hass.bus.async_listen(EVENT_STATE_CHANGED, self.handle_state_change)

        # 定时任务，每 3 秒发送一次状态变化
        self.hass.loop.create_task(self.periodic_send_state_changes())

    async def handle_state_change(self, event):
        ''' 处理实体状态变化事件 '''
        entity_id = event.data.get('entity_id')
        if entity_id in self.ha_mqtt.entities:
            new_state = event.data.get('new_state')
            if new_state:
                state_data = await self.ha_mqtt.get_state(entity_id)
                if state_data:
                    async with self.lock:
                        # 使用字典存储，确保同一实体只保留最新状态
                        self.state_changes[entity_id] = state_data

    async def periodic_send_state_changes(self):
        ''' 每 3 秒发送一次状态变化 '''
        while True:
            await asyncio.sleep(3)
            now = time.time()
            # 检查 receive_time 是否超过半小时
            if self.ha_mqtt.receive_time and (int(now) - self.ha_mqtt.receive_time > 1800):
                #_LOGGER.warning("超时未更新 receive_time，停止上报数据")
                continue  # 跳过本次循环，不发送数据

            async with self.lock:
                if self.state_changes:
                    # 获取所有状态变化
                    to_send = list(self.state_changes.values())
                    # 发送到云端                    
                    publish_data = {
                        'id': str(now),
                        'time': int(now),
                        'type': 'state_changed',
                        'data': to_send
                    }
                    topic = f'wxha/{self.ha_mqtt.topic}'
                    self.ha_mqtt.publish(topic, publish_data)
                    # 清空状态变化字典
                    self.state_changes.clear()