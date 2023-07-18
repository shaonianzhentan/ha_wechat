import urllib

async def async_generate_qrcode(hass, topic, token):
    key = f'ha:{token}#{topic}'
    qrc = urllib.parse.quote(key)
    await hass.services.async_call('persistent_notification', 'create', {
                'title': '使用【HomeAssistant家庭助理】小程序扫码关联',
                'message': f'[![qrcode](https://cdn.dotmaui.com/qrc/?t={qrc})](https://github.com/shaonianzhentan/ha_wechat) <font size="6">内含密钥和订阅主题<br/>请勿截图分享</font>'
            })
    await hass.services.async_call('persistent_notification', 'create', {
                'title': '发送以下数据到【HomeAssistant家庭助理】公众号关联',
                'message': key
            })