# HomeAssistant for Wechat

微信扫一扫，关注后使用微信控制HomeAssistant

<img src="https://github.com/shaonianzhentan/image/raw/main/ha_wechat/wechat-channel.png" height="160" alt="HomeAssistant家庭助理" title="HomeAssistant家庭助理"> 

[![hacs_badge](https://img.shields.io/badge/Home-Assistant-049cdb)](https://www.home-assistant.io/)
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
![visit](https://visitor-badge.laobi.icu/badge?page_id=shaonianzhentan.ha_wechat&left_text=visit)

[![badge](https://img.shields.io/badge/Conversation-语音小助手-049cdb?logo=homeassistant&style=for-the-badge)](https://github.com/shaonianzhentan/conversation)
[![badge](https://img.shields.io/badge/Windows-家庭助理-blue?logo=windows&style=for-the-badge)](https://www.microsoft.com/zh-cn/store/productId/9n2jp5z9rxx2)
[![badge](https://img.shields.io/badge/wechat-微信控制-6cae6a?logo=wechat&style=for-the-badge)](https://github.com/shaonianzhentan/ha_wechat)
[![badge](https://img.shields.io/badge/android-家庭助理-purple?logo=android&style=for-the-badge)](https://github.com/shaonianzhentan/ha_app)

[![badge](https://img.shields.io/badge/QQ交流群-61417349-76beff?logo=tencentqq&style=for-the-badge)](https://qm.qq.com/cgi-bin/qm/qr?k=aoYbEJzQ8MiieLhvQfhE_Ck1vLENuErf&jump_from=webapi&authKey=FT+TXsLXVNUtYY9G0q82vrBTxVT8axAg2C/tP9U1x9JioabEAbzVB7sPVGy/nIHN)
 
## 安装

安装完成重启HA，刷新一下页面，在集成里搜索`ha_wechat`即可

[![Add Integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start?domain=ha_wechat)

## 使用说明

本服务使用公共MQTT开放服务，消息采用加密传输，如遇异常信息，请联系我

**注意、注意、注意**
> 本项目依赖`语音小助手插件`

请先安装配置这个组件：https://github.com/shaonianzhentan/conversation

- 首先需要先关注公众号 `HomeAssistant家庭助理`
- 然后发送 `打开控制模式` 获取订阅主题和用户ID
- 发送命令后，进入控制模式
- 在HomeAssistant集成中添加本插件，配置相关信息
- 最后微信就可以发送控制命令啦
- 如果不想使用了，发送 `关闭控制模式`，然后卸载插件即可

微信教程：https://mp.weixin.qq.com/s/LTsE90PxkbWU9GvWxKJf1A

## 小程序

推荐使用微信小程序控制

注意：在iOS系统中无法添加小程序到桌面，这个时候可以使用支付宝小程序

|支付宝|微信|
|---|---|
<img src="https://github.com/shaonianzhentan/image/raw/main/ha_wechat/alipay.jpg" align="left" height="160" alt="支付宝小程序" title="支付宝小程序">  |  <img src="https://github.com/shaonianzhentan/image/raw/main/ha_wechat/wechat.jpg" align="left" height="160" alt="微信小程序" title="微信小程序">


## 功能截图
![img](https://github.com/shaonianzhentan/image/raw/main/node-red-contrib-ha-wechat/3.png)

## 如果这个项目对你有帮助，请我喝杯<del style="font-size: 14px;">咖啡</del>奶茶吧😘
|支付宝|微信|
|---|---|
<img src="https://github.com/shaonianzhentan/image/raw/main/ha_wechat/pay_alipay.png" align="left" height="160" alt="支付宝" title="支付宝">  |  <img src="https://github.com/shaonianzhentan/image/raw/main/ha_wechat/pay_wechat.png" align="left" height="160" alt="微信支付" title="微信">