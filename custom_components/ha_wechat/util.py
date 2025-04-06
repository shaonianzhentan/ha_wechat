import urllib
import qrcode
from io import BytesIO
import base64

async def async_generate_qrcode(hass, topic, token):
    """生成二维码并显示在通知中"""
    # 构造二维码内容
    key = f'ha:{token}#{topic}'
    qrc = urllib.parse.quote(key)

    # 生成二维码图片
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(key)
    qr.make(fit=True)

    # 将二维码转换为 Base64 编码
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    img_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

    # 在通知中显示二维码
    await hass.services.async_call('persistent_notification', 'create', {
        'title': '关联控制',
        'message': f'## 小程序扫码关联 <br/> ![qrcode](data:image/png;base64,{img_base64}) <br/> 内含密钥和订阅主题, 请勿截图分享'
    })