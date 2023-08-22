'''
小程序局域网通信协议
'''
import time, uuid
from homeassistant.components.http import HomeAssistantView
from .manifest import manifest

DOMAIN = manifest.domain

class HttpApi(HomeAssistantView):
    
    url = f'/api/{DOMAIN}'
    name = f'api:{DOMAIN}'
    requires_auth = False

    async def post(self, request):
        hass = request.app["hass"]
        body = await request.json()

        topic = body.get('topic')
        key = body.get('key')
        data = body.get('data')

        ha = hass.data[DOMAIN]
        if ha.key == key and ha.topic == topic:
            result = await ha.async_handle_data(data)
            if result is not None:
              msg_id = data.get('id')
              msg_type = data.get('type')
              return self.json({
                'id': str(uuid.uuid4()),
                'time': int(time.time()),
                'type': msg_type,
                'data': result
            })

        return self.json_message("没有权限", status_code=401)