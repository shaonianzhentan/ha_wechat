import urllib

async def async_generate_qrcode(hass, topic, token):
    ''' 生成二维码 '''
    key = f'ha:{token}#{topic}'
    qrc = urllib.parse.quote(key)
    await hass.services.async_call('persistent_notification', 'create', {
                'title': '关联控制',
                'message': f'## 小程序扫码关联 <br/> ![qrcode](https://cdn.dotmaui.com/qrc/?t={qrc}) <br/> 内含密钥和订阅主题, 请勿截图分享 <br/> <hr/> <h1>微信公众号关联</h1> 发送以下数据到【<b>HomeAssistant家庭助理</b>】公众号 <br/><br/> <mark>{key}</mark>'
            })