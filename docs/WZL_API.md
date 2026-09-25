# 微租林（weizulin / funjs）后端 · 接口实测记录

2026-09-25 从「许家去水印」（`wx2b979b1c16784d44`）包缓存逆向 + 真机实测。
**已端到端跑通签到**，全程无人工确认。

## 归属与识别

| 项 | 值 |
|---|---|
| 品牌 | 许家去水印（**工具类**：视频/图片去水印，签到送**使用次数**，不是餐饮积分） |
| appId | `wx2b979b1c16784d44` |
| 平台内应用 ID | `app-cf9187c281ff`（`x-appid` 头用它，**不是**微信 appId） |
| 后端 base | `https://saas.funjs.top/api` |
| 图片/CDN | `https://qny.weizulin.cn` |
| 框架 | Taro（React 系，包内是 webpack 打包产物） |

**域名与密钥怎么找到的**：包里 app.js 顶层直接有一个配置对象 —— 几乎是最省事的一次：

```js
var n = {APPID: "app-cf9187c281ff", BASE_URL: "https://saas.funjs.top/api", VERSION: "1.0.0"}
```

`qny.weizulin.cn` 只在图片 URL 里出现（接口不写它），所以只看域名清单会误判后端；
真正的 API host 是 `saas.funjs.top`。**提示：包内配置对象比域名清单更可信。**

## 认证：静默登录换 JWT，之后 Bearer

```js
// 登录（包内源码）
n.A({url: "/open/auth/mp/silent-login", method: "POST",
     header: {"x-appid": APPID}, data: {code}, skipAuth: true})

// 其余请求
n.A({url: "/open/check-in/status", method: "GET"})     // 封装里自动带
// → Authorization: Bearer <token> + x-appid + x-client-source: applet
```

- 无签名、无 nonce、无加密（请求体就是 JSON 明文）
- token 是 **JWT**，payload：`{"appUserId":71665,"applicationId":57,"merchantId":42,"tokenType":"open","iat":…,"exp":…}`
- 实测有效期 **7 天**（`exp - iat = 604800`）

## 完整链路（每步都实测）

### 1. 拿 code（在小程序逻辑层）

`wxcode.py`（通用取码器，与 `wxyd.py` 的区别见该脚本注释）：

```
WZ_JSON={"appid": "wx2b979b1c16784d44", "code": "0c1xXwGa11KOtM05LjJa14HcvA0xXwGJ"}
```

### 2. code 换 token

```
POST https://saas.funjs.top/api/open/auth/mp/silent-login
header: x-appid: app-cf9187c281ff / x-client-source: applet / Content-Type: application/json
body:   {"code": "<code>"}
```

实测返回：

```json
{"code": 0, "message": "ok", "data": {
  "openId": "ouock7cD6gmcAq5VhGQ1Uv-LIB-w",
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9…",
  "isNewUser": false,
  "user": {"id": 71665, "applicationId": 57, "merchantId": 42,
           "nickname": "狂暴星辰J3MX", "inviteCode": "4AE99255", "status": "enabled"}
}}
```

**服务端自己持 appsecret 调 code2session**（传假 code 会回微信的 `invalid code, rid: …`）。
`code` **一次一用**：重放会回 `{"code":400,"message":"code been used, rid: …"}`。

### 3. 查状态

```
GET /api/open/check-in/status    Authorization: Bearer <token>
→ {"code":0,"message":"ok","data":{
     "enabled": true, "todayChecked": false, "streakDays": 0,
     "nextRewardCount": 1, "daysToStreakBonus": 7,
     "dailyReward": 1, "streakBonusReward": 5, "streakBonusDay": 7,
     "memberType": "free"}}
```

未带 token → `{"code":401,"message":"未登录或登录已过期","data":null}`（HTTP 也是 401，
所以**判成败要看 body 的 `code`，不是 HTTP 状态码**）。

### 4. 签到

```
POST /api/open/check-in   {}    Authorization: Bearer <token>
→ {"code":0,"message":"ok","data":{
     "checkedIn": true, "streakDays": 1, "rewardCount": 1,
     "isStreakBonus": false, "bonusCountBalance": 1}}
```

复查 `status` → `todayChecked: true`、`streakDays: 1`、`daysToStreakBonus: 6` ✅

规则（从字段名可读出）：每日 +`dailyReward`(1) 次，连签第 `streakBonusDay`(7) 天额外 +`streakBonusReward`(5) 次。

## 可自动化程度

| 环节 | 需要人工？ |
|---|---|
| 打开小程序（拿 code） | 否 —— 引擎已有机制 |
| wx.login 取 code | 否 —— CDP 逻辑层执行 |
| 换 token | 否 |
| 签到 | 否 |

**唯一前提**：微信客户端本身在登录态（长期，不重启就不掉）。

## 复现步骤

```bash
# 1. 打开小程序（面板坏了也能开：主窗口顶部搜索框 → 输名字 → 点结果 → 用 appId 复核）
# 2. 逻辑层取 code
docker exec woc-wx-2ada0225ca python3 /tmp/wxcode.py wx2b979b1c16784d44 15
# 3. 换 token
curl -s -X POST "https://saas.funjs.top/api/open/auth/mp/silent-login" \
     -H "Content-Type: application/json" -H "x-appid: app-cf9187c281ff" \
     -H "x-client-source: applet" -d '{"code":"<code>"}'
# 4. 签到
TK=<上一步的 data.token>
curl -s -X POST "https://saas.funjs.top/api/open/check-in" \
     -H "Authorization: Bearer $TK" -H "x-appid: app-cf9187c281ff" \
     -H "x-client-source: applet" -H "Content-Type: application/json" -d '{}'
```

## 踩到的坑

- **`http=000` 十秒超时** ≠ 服务端拒绝：本环境 curl 走本地 CONNECT 代理
  （`127.0.0.1:…`），代理瞬时抖动就是这个症状（`time_total` 正好 ~10s）。
  隔几秒重试即恢复 —— 别把它当成「接口挂了」或「被风控」。
- **code 一次一用**：调试时别把 `wx.login` 的结果存下来反复试。
- ⚠️ **2026-09-25 22:4x 复查：服务端整体不可达**（直连 15s 超时、走本地代理 10s 超时，
  连试 3 次一致；DNS 正常解析到 `117.72.171.228`）。同一时刻易东 `zhyx.eingdong.com`
  直连 0.18s 正常 —— **所以不是本机网络问题**。链路上文 1~4 步连同「签到成功」都拿到过
  服务端返回，这里是服务端/路由侧的临时状态，与适配器无关。
- 排查这类「不通」时先分清两件事：本环境 `curl` 默认走 `http_proxy=127.0.0.1:…` 的
  CONNECT 隧道，而 Python 的 `http.client` 是**直连**（不读环境变量）。
  `http=000` + `time_total` 恰好 ~10s/15s 就是连接层超时，不是服务端拒绝
  （服务端拒绝会返回 4xx/5xx 带 body）。
