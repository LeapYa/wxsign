# 易东（eingdong.com）后端 · 接口实测记录

2026-09-25 从「刘一手火锅店」（`wx757e678caa41a2a6`）包缓存逆向 + 真机实测。
**已端到端跑通签到**，全程无人工确认。

## 归属与识别

| 项 | 值 |
|---|---|
| 品牌 | 刘一手（该 appId 是**巴塞罗那店**：`storeInfo.name = 巴塞罗那刘一手火锅店`） |
| appId | `wx757e678caa41a2a6` |
| 同族 | 巧娘子 `wxa8f4bf2bc3913d64`（同为易东，未实测） |
| 后端 host | `https://zhyx.eingdong.com` |
| 图片 host | `https://zhyx-images.eingdong.com` |
| 长连接 | `wss://ws.eingdong.com:1995`（多人点餐，与本流程无关） |

域名是**怎么找到的**：`pkg_domains.py` 从包正文（XOR 尾巴）里抠出带协议的 host，
再按 6 个家族归类。易东这一族的特征是接口形如 `/api/index.php/<controller>/<action>`。

## 认证：一个 cookie，几乎白盒

```js
wx.request({
  url: getApp().globalData.url + "/" + e,          // base + 明文路径
  method: "POST",
  header: { cookie: "sessionKey=" + getApp().globalData.sessionKey },
})
```

- base = `https://zhyx.eingdong.com/api/index.php`
- 认证 = **cookie `sessionKey=<sessionId>`**，无签名、无 nonce、无时间戳
- 包里出现的 `HMAC` 是打包进来的 crypto-js，与请求签名无关

## 完整链路（每步都实测）

### 1. 拿 code（在小程序逻辑层）

`wxeval.py --file expr_ident.js` 在逻辑层跑：

```js
wx.login({success: r => r.code})            // → 0c1lbsll2GXqui4fsenl2WUK3g0lbslO
wx.getExtConfigSync().storeid               // → "2165"
```

`storeid` 在第三方平台代管的 ext 配置里（`wx.getExtConfigSync()`），**不在包内**，只能运行时读。

### 2. code 换 sessionKey

```
POST /api/index.php/api/login
data: code=<code>&storeid=2165&ext_storeid=2165&v=1&parent_id=0
```

参数取自包内 app.js 的 `login_()`：
`{code, storeid, tableid, ext_storeid, user_temp_id, parent_id, v}`，
可选 `last_login_openid`（来自 `__yx_openid` storage）。

返回（实测）：

```json
{
  "openId": "oqk3Y5cr5zWXF2fO81QUQ-LyQt8A",
  "sessionId": "lb6rkaf765r6p06s0e629r0k67hdehqh",
  "user_id": "194362013",
  "vip": 0,
  "store_name": "巴塞罗那刘一手火锅店",
  "storeInfo": { "id": "2165", "appid": "wx757e678caa41a2a6", ... },
  "status": 1
}
```

响应头里 `Set-Cookie: sessionKey=...`。**服务端自己持 appsecret 调 code2session**，
我们不需要 appsecret —— 传一个假 code 会回微信的 `errcode:40029`，正好证明这一点。

### 3. 签到

```
POST /api/index.php/signin/get_info    cookie: sessionKey=<sk>
   → {"status":1,"info":{"b":"0","reward_type":"0","signin_log_list":[],
                         "signed_today":0,"keep_days":0}}
POST /api/index.php/signin/check_in_1  cookie: sessionKey=<sk>
   → {"status":1,"msg":"签到成功",
      "reward":{"reward_type":"0","extra_reward":0,"keep_days":1,"reward_amount":1,"coupon":[]}}
```

复查 `get_info` → `signed_today:1`、`keep_days:"1"`、
`signin_log_list[0].date = "2026-9-25"` ✅

**凭证行为（2026-09-25 二轮验证）**：

- **幂等**：当天重复调 `check_in_1` → `{"status":-1,"msg":"今天已经签到过了"}`，
  不会重复加积分。所以定时任务重复跑是安全的。
- **有效期**：`/api/login` 的响应头给的是
  `Set-Cookie: sessionKey=...; expires=... 14:25:13 GMT`（≈ 北京时间 22:25，约 **8 小时**）。
- **跨微信重启仍有效**：这个 sessionKey 是微信容器 `docker restart` **之前**取的，
  重启后（含用户在手机重新确认登录）**复查与签到都正常** ——
  因为它是易东服务端的会话，**与微信客户端的登录态无关**。
  也就是说：每天只需重新拿一次 code 换 sessionKey，之后的调用不受微信侧影响。

## 同族其他接口（从包内 `url:"..."` 清单提取，共 123 个唯一）

- 集章卡：`/Collect_badge/`、`/Collect_badge/get_detail`、`/Collect_badge/receive`
- 积分：`/integral/get_info`、`/integral/get_integral`
- 抽奖：`/lottery/me_join_processing`、`/lottery/me_join_over`
- 会员：`/vip/join`、`/vip/get_setting`、`/vip/new_renew`
- 登录相关：`/user/get_info`、`/api/update_user_info`、`/user/update_telphone`

## 可自动化程度

| 环节 | 需要人工？ |
|---|---|
| 打开小程序（拿 code） | 否 —— 引擎已有机制 |
| wx.login 取 code | 否 —— CDP 逻辑层执行 |
| 换 sessionKey | 否 |
| 签到 | 否 |

**唯一前提**：微信客户端本身在登录态（长期，不重启就不掉）。

## 复现步骤

```bash
# 1. 打开小程序（面板坏了也能开，见下）
#    主窗口顶部搜索框 → 输名字 → 点「最近使用过的小程序」里的目标 → 读 appId 校验
# 2. 逻辑层取 code + storeid
docker exec woc-wx-2ada0225ca python3 /tmp/wxeval.py --file /tmp/expr_ident.js
# 3. 换 sessionKey 并签到
curl -s -X POST "https://zhyx.eingdong.com/api/index.php/api/login" \
     -d "code=<code>&storeid=2165&ext_storeid=2165&v=1"
curl -s -X POST "https://zhyx.eingdong.com/api/index.php/signin/check_in_1" \
     -H "cookie: sessionKey=<sk>" -d "storeid=2165"
```
