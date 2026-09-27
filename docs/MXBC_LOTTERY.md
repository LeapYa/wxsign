# 蜜雪冰城（mxbc）· 每日抽奖调查记录

> 2026-09-27。**结论：链路已挖通 90%，但抽奖动作被 JS-challenge 反爬挡住，纯 HTTP 走不通。**

## 一、先说结论

- 用户说的**「每日抽奖」确实存在**，位置在**会员权益**里：
  「我的 → 会员权益」第 3 条 = **「雪王币抽奖」**（`buttonText:"立即抽奖"`，
  `activityType:1013`、`activityId:1963852076723355650`）。
- 活动规则原文（`projectRule.query` 返回）确认了用户说的「每天免费一次」：
  > 用户在活动期间**每天有 1 次免费抽奖的次数**，免费次数用完后，可消耗 **20 雪王币/次**继续参与抽奖，不限参与次数
- 承载页是**第三方（兑吧 duiba）的大转盘 H5**，不在蜜雪小程序包里：
  `https://76177-activity.dexfu.cn/galaxy/app/project/2924/index.html`
- **好消息**：身份链路**已完全打通并实测有效**，且**「今天还有没有免费次数」可以纯 HTTP 查**（已实测 `remainFreeTimes: 1`）。
- **坏消息**：**抽奖动作**（`luck/draw.do`）要求一个 **`token`**，它由兑吧自己的
  **JS-challenge**（`getToken.query` 返回 17KB 混淆 JS，每次不同，靠
  `eval(String.fromCharCode(...))` 现场生成）产出 —— **不是普通签名字符串**。

## 二、入口与归属

| 项 | 值 |
|---|---|
| 小程序 | `蜜雪冰城`，appId `wx7696c66d2245d107` |
| 入口 | 「我的」→ 会员权益 → **雪王币抽奖** |
| 权益接口 | `POST /v2/memberRights/mainPage/levels`（蜜雪侧，返回 `jumpUrl`） |
| 活动页 | `https://76177-activity.dexfu.cn/galaxy/app/project/2924/index.html` |
| 活动名 | 抽10000份雪王周边（`projectId=2924`，兑吧 `appId=76177`） |
| 活动期 | 2026-09-01 ~ 09-30 |
| 奖品 | 雪王百变盲盒 / 雪王挂件 / 88雪王币 / 8雪王币 / 1元零食券 / 2元饮品券 / 9折饮品券 / 谢谢参与 |
| 平台 | **兑吧（duiba）** spark 框架，网关 `x-service-id: PROJECTX-GATEWAY` |

> 首页活动区的「右二-抽雪王手办」也是同一个 URL —— 只是一个入口位。

## 三、身份链路（**已实测打通**）

```
① 蜜雪侧：拿 accessToken
     getApp().globalData.accessToken（JWT，逻辑层 CDP 可读）
        ↓
② 蜜雪侧：换兑吧免密登录 URL
     GET /v1/duiba/getLoginUrl?dbredirect=<活动URL>
        → data.loginUrl = https://76177-activity.dexfu.cn/autoLogin/autologin
             ?dcustom=…&redirect=…&uid=2104177381979123713&credits=0
             &sign=<32hex>&appKey=3AkRudni3MSyvsh89f745gEiPoYb&vip=1&timestamp=…
        ↓
③ 兑吧侧：访问 loginUrl → 拿到会话 cookie（**实测成功**）
     wdata3=<…>   tokenId=<32hex>   dcustom=<…>   isNotLoginUser=false
     最终落在 …/project/2924/index.html?from=login&spm=76177.1.1.1
```

⚠️ 小程序 `getApp().navigate()` 对 `http(s)://` 只会 `navigateTo('/pages/webView/index?url=…')`；
**只有 `needToken` 的 URL 才会注入 `accessToken`**。dexfu 走的是上面的 `getDBLoginUrl` 分支 ——
`h = url.indexOf("dexfu.cn") !== -1` 时调 `/v1/duiba/getLoginUrl`。

## 四、抽奖接口（兑吧侧）

前缀 = `/galaxy/app/project/2924/`（实测：`/luck/index.do` 返 200，其它前缀 404）

| 接口 | 作用 | 实测 |
|---|---|---|
| `luck/index.do` | 抽奖首页信息 | ✅ **code 000000**，含 `remainFreeTimes` |
| `luck/draw.do` | **抽奖**（`withToken`） | ❌ `P02140 Token校验失败` |
| `luck/deductCredits.do` | 花币抽（20 雪王币） | 需 token |
| `luck/queryStatus.do` | 查询状态（`withToken`） | 需 token |
| `luck/coopIndex.do` | 合作方首页 | ✅ code 000000 |
| `projectRule.query` | 活动规则 | ✅ 返回规则 HTML |
| `coop_frontVariable.query` | 合作方变量 | ✅ 返回 `{}` |
| `getTokenKey.query?projectId=2924` | **JS-challenge：key** | ✅ 返回混淆 JS |
| `getToken.query?projectId=2924` | **JS-challenge：token** | ✅ 返回 17KB 混淆 JS |

`luck/index.do` 实测返回（2026-09-27）：

```json
{"success":true,"code":"000000","data":{
  "credits":0,               // 雪王币余额
  "preConsumeCredits":20,    // 花币抽一次的价
  "hadPlayTime":0,           // 已抽次数
  "freeTimeNum":1,           // 每天免费次数上限
  "freeTimeUnit":1,          // 单位 = 天
  "remainFreeTimes":1,       // ★ 今天还剩 1 次免费
  "notLogin":false,
  "rewardList":[{"id":34962,"name":"88雪王币"},…]}}
```

## 五、卡点：JS-challenge（`getToken.query`）

`getToken.query` 返回的**不是 token 值**，而是一段**每次不同的混淆 JS**（≈17KB），形如：

```js
var __Pr4n = String.fromCharCode;
var _x_aYe = [3954,2465,1191,2775,3245];        // ← 每次随机
var _$ryu = function(){ return arguments[0] ^ _x_aYe[0]; };
…
eval(__Pr4n(32) + __Pr4n(-1-~(0x77^0), 0x69, 110, …) + …)   // ← 拼出代码再 eval
```

即：**服务端下发一段"计算 token 的代码"，前端 eval 得到 token**，再把 token 放进
`withToken` 接口的请求参数（`t.token = i`，见 `main.js` 的请求封装）。

- 错误码映射里也有 `220001 GET_PX_TOKEN_FAILED`（承自兑吧公共库；页面**没有**加载
  PerimeterX 的 SDK，所以这条只是枚举，不是真 PX）。
- 复刻代价：需要 **Node 动态 eval** 这段 JS；而它可能引用浏览器对象、
  且每次不同 → 脆弱、且一旦行为异常**正落在风控的判定范围内**。

## 六、⚠️ 风控与规则（必须知情）

活动规则原文（`projectRule.query`）明确写着：

> 任何参与活动的用户不得以任何**机器人软件、爬虫软件、刷屏软件**或任何非人工方式参与活动，
> **一经发现立即取消领奖资格**，且该用户于活动中获得的奖励全部收回

与「签到」类接口（服务端只校验签名/登录态）不同，兑吧这套是**有反爬投入**的
（JS-challenge + 可能需要浏览器指纹）。**自动化抽奖属于规则明确禁止的行为**。

## 七、可选的下一步（都还没做）

1. **只自动化「查询」**：`luck/index.do` 纯 HTTP 可读 → 可以每天查一次
   「今天还有没有免费次数 / 中了什么」，**不触发抽奖动作**，风险最低。
2. **驱动 H5 抽奖**：把活动页放进 web-view / 无头浏览器，让页面自己算 token 并点抽奖
   （即「不碰算法、只驱动界面」的老路，参考 `docs/OPPO_API.md` 的备选通道）。
3. **Node eval 复刻 token**：技术上可行，但违背规则且脆弱 —— 不建议。

## 八、复现命令（都已实测）

```bash
# 1) 蜜雪侧：读 accessToken（容器内，CDP 读逻辑层 getApp()）
#    三段就够：wxcdp.WS() → wxnet.find_logic_ctx(ws, appid) → wxdom.evaluate(ws, expr, ctx)

# 2) 换兑吧免密登录 URL（蜜雪 API，需签名 —— 实现见下）

# 3) 用 loginUrl 换 cookie
curl -L -c cookies.txt "<loginUrl>"

# 4) 查抽奖首页（纯 HTTP，不需要 token）
curl -b cookies.txt "https://76177-activity.dexfu.cn/galaxy/app/project/2924/luck/index.do"

# 5) 抽奖（需要 token，当前会回 P02140）
curl -b cookies.txt "…/luck/draw.do?ticketNum="
```

### 蜜雪签名复刻（Python 版，实测 `/v2/memberRights/mainPage/levels` 等均 code=0）

```python
import hashlib, json, time, urllib.request

BASE = "https://mxsa.mxbc.net/api"
VER = "2.8.58"
SALT_WEIXIN = "0d787c102fe2f7b4279af8925819d5fd"
APPID_WEIXIN = "d82be6bbc1da11eb9dd000163e122ecb"   # config/index.js


def create_str_before_sign(d):                       # utils/index.js
    out = []
    for k in sorted(d.keys()):
        v = d[k]
        if v is None or v is False or v == "":
            continue
        if isinstance(v, (dict, list)):
            v = json.dumps(v, separators=(",", ":"), ensure_ascii=False)
        out.append("%s=%s" % (k, v))
    return "&".join(out)


def enhance_md5(params):                             # utils/enhanceMD5.js
    p = dict(params)
    p.pop("sign", None)
    p["appId"] = APPID_WEIXIN
    p["t"] = int(time.time() * 1000)
    p["s"] = 3
    md5hex = hashlib.md5(
        (create_str_before_sign(p) + SALT_WEIXIN).encode("utf-8")).hexdigest()
    b = bytes.fromhex(md5hex)
    parts = []
    for i in range(4):                               # 4 组「有符号大端 int」
        v = (b[4*i] << 24) | (b[4*i+1] << 16) | (b[4*i+2] << 8) | b[4*i+3]
        if v >= 0x80000000:                          # JS 位运算 = 有符号 32 位
            v -= 0x100000000
        parts.append(str(0x7FFFFFFF if v == -0x80000000 else abs(v)))
    return md5hex + "".join(parts), p                # sign = md5hex + 拼接值


def call(path, params, token, cid="", method="POST"):
    sign, body = enhance_md5(params)
    body = dict(body, sign=sign)
    url = path if "://" in path else BASE + path     # 带 :// 者直用（原码 request/index.js）
    headers = {"Content-Type": "application/json", "version": VER,
               "Access-Token": token, "x-ssos-cid": cid or "", "traceNo": ""}
    if method == "GET":
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(body)
        data = None
    else:
        data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    return urllib.request.urlopen(req, timeout=25).read().decode()
```

- `t` 要与服务端时间对齐：`/v1/app/config` 返回 `timestamp`，原码算 `timeOffset = Date.now() - timestamp` 来校正。
- 无 `Access-Token` 调业务接口 → `code=500 未知异常，请联系管理员`（不是 401）。
