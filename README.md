# ha_wechat
使用微信控制HomeAssistant

[![hacs_badge](https://img.shields.io/badge/Home-Assistant-%23049cdb)](https://www.home-assistant.io/)
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
![visit](https://visitor-badge.laobi.icu/badge?page_id=shaonianzhentan.ha_wechat&left_text=visit)

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
- 如果不想使用了，发送 `关闭控制模式`

微信教程：https://mp.weixin.qq.com/s/LTsE90PxkbWU9GvWxKJf1A

## 功能截图
![img](https://cdn.jsdelivr.net/gh/shaonianzhentan/image@main/node-red-contrib-ha-wechat/3.png)

## 如果这个项目对你有帮助，请我喝杯<del style="font-size: 14px;">咖啡</del>奶茶吧😘
|支付宝|微信|
|---|---|
<img src="https://ha.jiluxinqing.com/img/alipay.png" align="left" height="160" width="160" alt="支付宝" title="支付宝">  |  <img src="https://ha.jiluxinqing.com/img/wechat.png" align="left" height="160" width="160" alt="微信支付" title="微信">

#### 关注我的微信订阅号，了解更多HomeAssistant相关知识
<img src="https://ha.jiluxinqing.com/img/wechat-channel.png" height="160" alt="HomeAssistant家庭助理" title="HomeAssistant家庭助理"> 

---
**在使用的过程之中，如果遇到无法解决的问题，付费咨询请加Q`635147515`**