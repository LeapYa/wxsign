# 荣耀商城（honor.com）后端 · 接口实测记录

> 首次打通 2026-09-28。这是本项目**唯一一个「签到不在小程序里」**的后端 ——
> 签到本体是**任务中心 H5**，由小程序页面用 web-view 承载。
> 但**整条链路都不需要打开那个 H5**（见第三节）——顺带说清：**「取凭证不驱动界面」
> 不是荣耀独有的**，八家的日常取凭证都如此（见 README 第五节的对照）；
> 荣耀独有的是**「会话只活 3 天」**，所以只有它需要凭证失效自愈。

## 一、结论（先看这段）

- **荣耀商城小程序本身没有签到。** 整包 grep「签到 / 打卡 / 赚积分 / 领积分 / 每日任务 /
  任务中心 / 签到有礼」**零命中**（只剩 5 处孤立 i18n 定义，**无使用处**），
  tabBar 只有 `pages/index` / `classify` / `personal`。**别再在小程序里找入口。**
- 签到在**任务中心 H5**（`www.honor.com/cn/msale/mp/jobcenter.html`），
  由小程序页面 `packageActivity/pages/login4Qxmp/login4Qxmp`（标题「任务页面」）承载。
- ⭐ **但不需要打开那个 H5**。三件事各自都能纯算：
  1. **`activityCode`（每期变）** ← 一次普通 GET 拉 H5 的 HTML，正则提内联属性
     `data-activity-code`。**不要 cookie、不要打开页面** ⇒ 换档期**零人工**。
  2. **cookie** ← `Network.getAllCookies` 是 **browser 级**命令，
     attach 到**任意** target（哪怕只是小程序的 `page-frame.html`）就能读整份 cookie jar
     ⇒ **不碰界面就能取身份**。
  3. **两个 POST** ← 纯 HTTP（`honor_post`）。**没有签名、没有 nonce。**
- 认证比想象中松：**`CsrfToken` 头可带可不带**（见第三节），已签路径上 `variedData` 也不必需。
- ⚠️ **凭证只活 3 天**（`euid` / `encryptRtNew`：登录时刻 + 3 天，**不滑动续期**）——
  引擎在过期时会**自动重新登录**；那一步依赖微信原生授权框，是「尽力自愈」（见第六节）。
- 幂等判据用 `queryTaskCenterInfo` 的 `result.signInInfo.signInToday`（服务端背书），
  服务端对重复签到另回专门码 `task.center.today.aready.signin`（双保险）。

## 二、归属与识别

| 项 | 值 |
|---|---|
| 小程序 | `荣耀商城`，appId `wx06a8d8c84a18be25` |
| 承载签到的页面 | `packageActivity/pages/login4Qxmp/login4Qxmp`（标题「任务页面」） |
| 承载签到的 H5 | `https://www.honor.com/cn/msale/mp/jobcenter.html`（**引擎里不带 `?version=`**） |
| API 主机 | `https://openapi-cn.c.honor.com` |
| 埋点主机 | `https://dap-cn.c.hihonor.com`（与业务无关，可忽略） |
| 签名 / 风控 | 无签名；有极验 `geetest`（**签到链路不经过它**，见第四节） |
| 档期（2026-09-28 实测） | `activityCode = QDHDz5SM67T65XPOBMBBQ1`，`2026-09-30 23:59:59+0800` 结束 |
| 签到周期 | **5 天**：`dailyRewards = {1:8, 2:9, 3:11, 4:13, 5:17}` |

> ⚠️ 别把「荣耀商城」和「我的华为」/「华为商城」搞混：华为商城（`wx208389d90830e5b7`）
> **没有签到**（详见 README 第七节第 10 条）；微信里**也没有**「我的华为」小程序。
> 荣耀商城是**独立品牌**（honor.com），不是华为的分身。

## 三、凭证：`.honor.com` 的 cookie（而且能「离线」读）

### 有哪些

| cookie | httpOnly | 长度 | **TTL（实测）** | 作用 |
|---|---|---|---|---|
| `euid` | ✅ | 67 | **3 天** | 用户 ID（**关键**） |
| `encryptRtNew` | ❌ | 305 | **3 天** | 加密的 refresh token（**关键**） |
| `CSRF-TOKEN` | ❌ | 42 | **会话级**（随浏览器关） | CSRF 令牌（实测签到**不需要**它） |
| `uid` / `user` / `name` | ❌ | 19 / 11 / 11 | 3 天 | 展示用（`user` = 脱敏手机号） |
| `hasLogin` / `hasSigned` | ❌ | 13 / 1 | 3 天 | 前端状态位 |
| `variedData` | ❌ | 2081+ | 7 天 | 设备指纹（签到 body 里带） |
| `deviceid` / `TID` | ❌ | 32 / 32 | 400 天 | 设备 / 会话标识 |
| `_areacode` / `HShop-AB` | ✅ | 2 / 1 | 3 天 | 区域 / AB 实验 |

合计 23 条（去重后）。

### ⭐⭐ 但先搞清一件事：荣耀有**两套会话**

这是本后端最容易搞错的地方（我实打实踩了）：

| | 存在哪 | 有效期 | 谁在用 |
|---|---|---|---|
| **华为账号会话** | cookie：`id1.cloud.huawei.com` 的 `sid` / `hwid_cas_sid` | **很持久** | 小程序「我的」页显示登录态的依据；SSO 的根 |
| **业务会话** | cookie：`.honor.com` 的 `euid` / `encryptRtNew` | **只活 3 天** | **签到**用它 |

**实测证据**（这组对照很说明问题）：
1. 把 `.honor.com` 的 6 个登录 cookie **全删掉** → 小程序「我的」页**照样显示已登录**
   （`190****7634 … 解除绑定并退出`）。
2. 连小程序 **storage** 里的 `euid` / `userLoginStatus` / `tid` 也删掉，再 `reLaunch` →
   **还是显示已登录**。
3. 但同一时刻，用剩下的 cookie 纯 HTTP 调接口 → **`{"code":"user.not.login","numCode":2001}`**。

→ 结论：**「我的」页的登录态 ≠ 签到的登录态**。页面认的是华为账号 SID；
签到只认 `.honor.com` 的业务 cookie。

> ✅ **这个差别带来一个好消息**：既然 SID 还在，业务 cookie 过期就**不是死路** ——
> 打开一次 honor.com 的页面，服务端会自动补发一套新的（见第六节 SSO 自愈）。

> ⚠️ **关键：`euid` / `encryptRtNew` 只活 3 天，而且不滑动续期。**
> 实测：登录后 **半小时内发了十几次业务请求**（H5 reload、`queryTaskCenterInfo`…），
> 它们的 `expires` 始终钉在「登录时刻 + 3 天」（`2026-10-01 11:53` = 登录时刻 `11:53:35` + 3 天），
> **一次都没往后挪**。所以这个后端**不是「登录一次就永久」** ——
> 引擎必须自己处理过期（见第六节）。

### ⭐ 怎么读到它 —— **不用打开任何页面**

```
Target.getTargets        → 找一个 *小程序的* page target，例如
                           page | AppIndex | https://servicewechat.com/wx06a8d8c84a18be25/118/page-frame.html
Target.attachToTarget({targetId, flatten:true})   → sessionId
Network.getAllCookies    （带上那个 sessionId）    → **整份** cookie jar
```

**关键点：`Network.getAllCookies` 是 browser 级命令** —— 它返回的是**整个浏览器**的 cookie
（实测 80 条，横跨 `.honor.com` / `.opposhop.cn` / `.mxbc.net` / `.pin-dao.cn` …），
**与 attach 到哪个 target 无关**。所以哪怕任务中心 H5 根本没打开，也照样读得到。

> 这条也是「为什么不用另一种做法」的答案：磁盘上
> `radium/web/profiles/webview_<hash>/Cookies` 里**确实**存着这些 cookie，
> 但值是 Chromium 的 **`v10` AES-GCM 密文**（解它要 keyring key）—— 不值得。
> 走 CDP 读出来是**明文**，一步到位。

### ⭐ 反向也一样：`wxlogin` 换来的 cookie 会**写回浏览器**（2026-09-28 补）

`Network.setCookies` 与 `getAllCookies` **对称** —— 同样作用在**浏览器级** cookie store 上。
所以 `wxhonor.py wxlogin` 拿到新 cookie 后会直接写回（`write_jar()`），好处很实在：

| | 不写回（原来） | 写回（现在） |
|---|---|---|
| 下次 `ident` | 读到「未登录」→ 又要走一遍自愈（多一次 `wx.login` + 一次 POST） | **直接读到登录态** |
| 依赖 | 逻辑层必须可用（hook 通、`wx.login` 能用） | **只要 CDP 连得上**（完全不碰逻辑层） |
| 引擎耗时 | ~60 秒 | **~33 秒**（实测） |

实测对照（同一天、同一账号）：

```
写回前 ident：  cookie 16 条：euid=**无**  encryptRtNew=**无**   → 登录态：**未登录**
跑 wxlogin：    wxQuickLogin → code=0 msg=login success（Set-Cookie 22 条）
                已写回浏览器 10 个 cookie ✅
写回后 ident：  cookie 22 条：euid=有  encryptRtNew=有            → 已登录 ✅ 凭证还剩 3.0 天
```

> ⚠️ 两个细节：
> 1. **登录过程的临时 cookie（`code` / `type`）不写回** —— 它们是 `wxQuickLogin` 自己的中间态，落地没意义。
> 2. `euid`（httpOnly）与 `encryptRtNew` **不在 `Set-Cookie` 里**（在响应 body 里），
>    写回时按实测的 **3 天 TTL** 单独补；其余 cookie 的属性（`Domain` / `Path` / `Secure` /
>    `HttpOnly` / `Max-Age` / `SameSite`）从 `Set-Cookie` 原样解析，尽量还原服务端意图。

### ⭐ `CsrfToken` 就是 cookie 里的 `CSRF-TOKEN`

H5 的 `window.csrftoken` 与 cookie 的 `CSRF-TOKEN` **实测完全相同**
（都是 `L0VnV2pJeFokWlRqN2VmWnV6RnBFWEN1anRFdVdDMQ`）。原因是 `csrftoken.js` 本身
只是个按域名返回**硬编码串**的函数：

```js
func getToken() {
    var wDomain = ".hihonor.com;.hicloud.com;.honor.cn";
    for (var i = 0; i < wds.length; i++) {
        if (url.endsWith(wds[i])) { csrftoken = "UXNvVjlCcDEkeDlFY21JYzBlNm1XeXhaaUFrYmJVMA"; break; }
    }
}
```

（顺带说明：**不必**去读页面变量 —— cookie 里就有。而且它是个会过期的场景值，
每次从 cookie 读才是对的。）

**实测：这个头可带可不带** —— 查询与签到接口在「带 / 空 / 完全不发」三种情况下返回一致。
代码里仍原样带上以保真（真签到路径万一校验它，不至于掉坑）。

## 四、完整链路（纯 HTTP，每步实测）

```http
# ① 幂等：查任务中心状态（认证只需 cookie）
POST https://openapi-cn.c.honor.com/tdcs/taskcenter/queryTaskCenterInfo
Content-Type: application/json
Cookie: euid=…; encryptRtNew=…; CSRF-TOKEN=…; variedData=…; …
CsrfToken: <同 CSRF-TOKEN>
Origin: https://www.honor.com
Referer: https://www.honor.com/cn/msale/mp/jobcenter.html

{"activityCode":"QDHDz5SM67T65XPOBMBBQ1","taskPortal":"4","beCode":"CN"}

→ {"code":"0","result":{ ..., "signInInfo":{ ..., "signInToday":true,
     "continuousSignIn":1, "signInCycle":5,
     "cycleSignInInfoList":[{ "currentDay":1790524800000, "earnPoint":8,
                              "signInToday":true, "today":true,
                              "todaySignInCanEarnPoint":8, "whetherSupplySignIn":1 }, …],
     "dailyRewards":{"1":8,"2":9,"3":11,"4":13,"5":17}, … } }}
```

```http
# ② 签到
POST https://openapi-cn.c.honor.com/tdcs/taskcenter/taskCenterSignIn
（同样的头）

{"activityCode":"QDHDz5SM67T65XPOBMBBQ1","taskPortal":"4",
 "agent":"<User-Agent>",
 "oas_refer":"https://www.honor.com/",
 "variedData":"<cookie variedData>"}

→ 成功：     {"code":"0","result":{ "earnPoint":8, ... }}
→ 今天已签： {"code":"task.center.today.aready.signin",
              "msg":"TASK CENTER TODAY AREADY SIGNIN","numCode":3027,"success":false}
```

> **参数是从 H5 的 `sign_in_interactive.js` 里读出来的**（不是猜的）：
> ```js
> const data = { activityCode, taskPortal, agent: window.navigator.userAgent,
>                oas_refer: window.location.origin + '/',
>                variedData: utils.cookie.get('variedData') ? utils.cookie.get('variedData') : null }
> const { code, result } = await request('POST', '/tdcs/taskcenter/taskCenterSignIn', data, headers)
> ```
> 它那边的 `headers` 只有 `Content-Type`（`CsrfToken` 由全局 `request()` 封装加）。

### 返回码语义

| code / numCode | 含义 | 引擎怎么处理 |
|---|---|---|
| `"0"` | 签到成功 | `(True, "200", …)` |
| `task.center.today.aready.signin` (3027) | 今天已经签过 | 视为完成 `(True, "415", …)` |
| `task.center.not.in.active.time` | 不在活动时间内 | 失败（前端会自己再判开始/结束） |
| `task.center.portals.err*` | portal 不对 | 失败（我们固定 `"4"` = 小程序） |
| 含 `risk` / `verify` / `captcha` / `geetest` | 风控 / 要验证码 | 报 `RISK`，**不重试** |

### 同一份 H5 里还有这些接口（**签到不需要**，列出来免得后人绕路）

| 接口 | 用途 |
|---|---|
| `POST /tdcs/taskcenter/taskCenterSupplySignIn` | **补签**（body `{activityCode, supplySignInDay, taskPortal}`） |
| `POST /tdcs/taskcenter/taskCenterValidateRisk` | 风控校验 —— ⚠️ **只给「任务」用，签到不经过它** |
| `POST /tdcs/geeTest/register` · `/tdcs/geeTest/validate` | 极验验证码 |
| `POST /tdcs/taskcenter/receiveReward` | 领奖 |
| `POST /tdcs/taskcenter/queryAssistCode` | 助力码（分享任务） |
| `POST /tdcs/taskcenter/operateTaskCenterSignInSwitch` | 签到开关 |
| `POST /ams/taskcenter/completeTask` | ⚠️ **这是「任务」的接口，不是签到**（浏览/分享/小游戏） |

> ⚠️ **`/ams/taskcenter/completeTask` 是个陷阱**：它出现在**小程序包**里
> （`m.mpPromisePost(service.openApiDomain + "/ams/taskcenter/completeTask", a, {CsrfToken:e})`），
> 参数长这样 `{taskPortal:"4", taskCenterId, taskId, subTaskId, completeType}`。
> 我在挖签到的时候先在包里 grep 到了它，一度以为这就是签到接口。**它不是** ——
> 它是「做任务」的（浏览商详页 / 分享 / 小游戏）。真正的签到在 H5 里，
> 接口是 `/tdcs/taskcenter/taskCenterSignIn`。
> （顺带：那个 `taskCenterId` 的实际取值就是 `activityCode` —— 见 `sign_in_interactive.js` 里
> `` link += `&taskId=${taskCode}&taskListCode=…&taskCenterId=${activityCode}` ``。）

## 五、`activityCode` 每期会换 —— 但零人工（纯 HTTP 提取）

`activityCode`（如 `QDHDz5SM67T65XPOBMBBQ1`）**不在 URL 上**，是页面 HTML 里内联的组件配置：

```html
<div class="J_mod sign-in-style4 mod-830038 mod-sign-in"
     data-component="4"
     data-activity-code="QDHDz5SM67T65XPOBMBBQ1"
     data-supply-link="https://www.hihonor.com/cn/asale/scbuqian.html" …>
  …
  <div class="main-btn" data-button-text="签到" data-click="1"
       data-action-code="800040031" data-action-name="签到组件签到点击"
       data-activity-id="QDHDz5SM67T65XPOBMBBQ1">签到</div>
</div>
```

所以引擎就一句正则：

```python
HONOR_ACTIVITY_RE = re.compile(r'data-activity-code="([^"]+)"')
# GET https://www.honor.com/cn/msale/mp/jobcenter.html   → 在这个 HTML 上 search
```

**实测要点**：
- **不需要 cookie、不需要登录、不需要打开 H5** —— `jobcenter.html` 是公开页。
- **`?version=` 可以省**：带与不带拿到的 HTML 同字节（48747），`activityCode` 一样。
  所以这一层也不依赖小程序下发的版本号 —— 纯算。
- 换档期后**什么都不用改**，下次跑就自动是新 code。

> ⚠️ 反之，两个**不能**拿来判状态的坑：
> 1. `.main-btn` 在 HTML 里写死文字是「签到」，运行时（已签状态）JS 会把它改成
>    「领更多积分」—— **按文案判断今天签没签必然错**，要用 `signInToday`。
> 2. `.continuous-days` 那个 `SPAN`（「已签到 1 天」）也是运行时渲染的，同理别当判据。

## 六、登录与续期

### ⭐⭐⭐ 最优路径：**纯 API 登录**（`wx.login` 的 code → 换 cookie）—— 零 UI

**这一条几乎把「登录」这个词的复杂度降到零**：不用点按钮、不用弹窗、不用开页面。

包内 `mpUtils` 的请求拦截器里明写着（不是猜的）：

```js
if (-1 == n.url.indexOf("mcp/account/wxQuickLogin")) { t.next = 5; break }
return t.next = 3, getApp().globalData.mpUtils.requestWXLoginCode();   // = wx.login()
case 3:
  n.data.code = t.sent;                                                  // ← code 塞进请求体
...
mpPromisePost(openApiDomain + "/mcp/account/wxQuickLogin", e, {CsrfToken: t})
  → t.data.unionId / t.data.refreshToken
```

**实测（只带 code，`iv` / `encryptedData` 都留空）**：

```http
POST https://openapi-cn.c.honor.com/mcp/account/wxQuickLogin
Content-Type: application/json
CsrfToken: <storage 里的 ct>
Origin: https://www.honor.com

{"code":"<wx.login 的 code>","timeout":10000,"type":"wxMiniProgram","country":"CN",
 "portal":4,"lang":"zh-CN","iv":"","encryptedData":"","userName":"","headImgUrl":"","loginUrl":""}

→ 200  {"code":"0","msg":"login success",
        "euid":"7ce15ea8…",
        "openId":"ogZ9B5QvKGnm5nfmhkWTH88MVI-8",
        "refreshToken":"secvmall00012ETMsDgAAAaDmVOvU…"（305 字符）}
   Set-Cookie（22 条）：euid / uid / user / hasphone / hasmail / __ukmc / rush_* / HShop-AB …
```

**关键对应关系**：响应 body 里的 **`refreshToken`（305 字符）就是 cookie 里的 `encryptRtNew`**
（格式完全一样，都是 `secvmall00012ETMsDgAAAaDm…`）。拼起来就是一套可用凭证：

```
cookie = Set-Cookie 的（euid / uid / user / hasphone / hasmail / __ukmc / rush_* / HShop-AB）
       + encryptRtNew = <body 的 refreshToken>
       + variedData   = <浏览器 cookie 里的设备指纹>
```

**实测：这套 cookie 直接能调 `queryTaskCenterInfo`（HTTP 200 + 正常数据）** —— 也就是**能签到**。

> ⚠️ **前提**：只带 `code` 能通，是因为接口靠**微信 unionId** 认人（所以它叫「快捷登录」）——
> 这个微信**以前绑定过**荣耀账号。**没绑定过**会失败，那一步绕不开 `getPhoneNumber`。
>
> 💡 包内同一条链路还有**另一种**用法：把 `getPhoneNumber` 拿到的 `iv` / `encryptedData`
> 一起带上 → 一次性完成「注册 + 登录」。**那条我们不做** —— 微信强制
> `getPhoneNumber` 必须由用户点击事件触发（见下），做不到纯 API。
>
> 📌 **一句话**：**已绑定的账号，登录完全不需要界面**；只有「首次把这个微信绑到荣耀账号」
> 才需要一次手机号授权。
>
> ⭐ 而且换来的 cookie 会**写回浏览器**（`Network.setCookies`，见第三节）—— 所以 3 天
> 有效期内 `ident` 直接就读到登录态，**连 `wx.login` 都不用调**（引擎从 60 秒降到 33 秒）。

## 六·补、UI 登录（`wxhonor.py login`）—— 只作兜底

**微信手机号授权 = 自动注册 / 登录荣耀账号**（包内文案原文）：

> 「点击同意后，我们将申请获取您的**微信手机号**用于绑定登录或注册荣耀账号」

比华为商城好一截 —— **不需要手工填手机号 + 短信码 + 密码**，而且微信手机号授权是**静默**的
（不用你收短信）。

⚠️ **要分清两件事**（我一开始把这两件混了）：
- **绑定**（账号关联）= **永久** —— 授权一次，服务端就记住「这个微信 = 这个荣耀账号」。
- **会话**（cookie 里的 `euid` / `encryptRtNew`）= **只活 3 天**（见第三节）。

所以「每隔几天要人工来一次」**并不成立** —— 引擎会自己重登录（见下）。

链路：`我的` 页 → 点「点击账号登录」 → 弹**微信原生**手机号授权框 → 点「允许」。

全自动、**零硬编码坐标**：

| 步骤 | 判据 |
|---|---|
| 先切到「我的」页 | 逻辑层 `wx.switchTab`（登录入口**只在那页上**，引擎带着过期 cookie 进来时小程序常停在首页） |
| 隐私声明「同意」 | 类名 **`.confirm_btn`**（外层 `AuthorizeModal-*`） |
| 登录入口 | 类名 `[class*=u-login]` + 文案「点击账号登录 / 立即登录 / 去登录」 |
| 微信原生「允许」框 | **像素判据**（逐行扫绿色、按 x 中心聚类、宽 100~260 高 28~80） |
| 页面坐标 → 屏幕坐标 | **窗口原点 + 逻辑层 `safeArea.top`**（⚠️ 不是 `outerHeight-innerHeight`，见第七节） |

### ⭐⭐ 引擎怎么处理凭证失效：**四级自愈**（`do_sign_honor`）

```
① 读 cookie 时顺带算 expires_in（关键 cookie 的最小剩余秒数）
② has_login==False 或 expires_in<=0 →
     ① **纯 API 登录**：wx.login() → code → POST /mcp/account/wxQuickLogin
        → 换回**全新一套** cookie（euid / encryptRtNew / …）
        ✅ **零 UI、零弹窗、零页面** —— 最快最纯，首选
     ② **SSO**：逻辑层 navigateTo 任务中心 → 让 honor.com 域发请求
        → 服务端借**华为账号 SID** 自动补发新 cookie
        ✅ 零弹窗，但要等 H5 加载（~20s）
     ③ **login**：点「点击账号登录」+ 抓**微信原生「允许」框**（手机号授权）
        ⚠️ 唯一需要弹窗的一级，只在 ①② 都失败时才走到
     ④ 都不行 → 报 NOLOGIN（给出人工命令）
③ 另外：接口回非 0（凭证被提前失效）→ 也补一次 ① + 重试一次
```

**实测（把 `.honor.com` 的 7 个登录 cookie 全删掉模拟到期）**：

```
[ident] 没有登录凭证 → ① 先试 **纯 API 登录**（wx.login 的 code → 换 cookie，零 UI）
  [honor] wx.login code = 0f1CIU2w3DKuO73L…（32 字符）
  [honor] wxQuickLogin → code=0 msg=login success（Set-Cookie 22 条）
  [honor] 拼出 11 个 cookie：… euid(67), encryptRtNew(305), variedData(1655)
  [honor] 纯 API 登录成功 ✅ 账号 190****7634 / uid 8550086500223209739
[ident] 凭证已恢复
[act] activityCode = QDHDz5SM67T65XPOBMBBQ1
RESULT honor code=415 msg=今日已签到（连续 1/5 天，累计 8 分）
```

> ✅ **所以「3 天过期」这件事已经彻底不成问题** —— 第一级就是纯 API 登录，
> 几秒钟搞定，且**不依赖任何界面**。后两级（SSO / login）只是兜底。

> ⚠️ **`login` 是整条链路里唯一需要「界面」的地方**（上面那条 SSO 路径不需要），
> 它依赖那个**微信原生手机号授权框**出现并被点中。实测：
> - 框**有 1~2 秒延迟**（点完登录入口不是立刻弹）；
> - 生存窗口约**半分钟**，不理会就没了；
> - 短时间反复触发会**疑似被限流**（点登录入口不弹框），隔几分钟恢复。
>
> 所以它是「**兜底**」而不是主力 —— 主力是 SSO。**但失败不会静默**：
> 会明确报 `NOLOGIN` 并在日志里给出人工命令 `python3 wxhonor.py login`。
>
> 💡 也正因如此，**别在 `login` 中间插别的命令**（插一次就可能错过窗口期）。

## 七、我在这条线上犯过的错（按时间顺序）

1. **以为荣耀 = 华为的小号，先去啃了华为商城。** 结论：华为商城小程序**根本没有签到**
   （README 第七节第 10 条列了四条证据）。白花了一轮。
   → 教训：**先确认这个小程序「有没有签到」，再谈怎么签**。判据是包内 grep + 登录后实地翻菜单，
   别信搜索引擎的「华为商城小程序有每日签到」（那说的是 App）。
2. **以为签到在「我的」菜单里。** 翻遍了（订单 / 拼团 / 售后 / 优惠券 / 积分 / 地址 / 消息），
   只有「我的积分」→ 会员积分页 → 「积分规则」H5 里写着签到在别处。
3. **以为签到请求走小程序逻辑层的 `wx.request`。** 装了 `wxnet.py` 去拦，**一条都没拦到**。
   → 因为**签到在 H5 里发**。小程序侧那些 `/ams/taskcenter/completeTask` 是**任务**用的。
   这是全篇最关键的一次纠偏。
4. **第一版实现是「attach H5 → 在页面内 fetch」。** 能用，但它把「打开那个 H5」变成了硬依赖 ——
   而 H5 只有在用户点了「全部任务 / 赚积分」之后才会存在。
   → 后来发现 **`activityCode` 能纯 HTTP 从 HTML 提、cookie 能 browser 级读**，
   于是整条链路撤掉界面依赖，改成纯 HTTP。**同样的接口，架构干净一截。**
5. **按「元素最多的 ctx」找 H5**（`find_dom_ctx`）→ 必然选到残留搜索页。
   → 正解：**先说要找什么**（`find_ctx_with([...])`），再看哪个 ctx 有。
6. **`wxreg` 的坐标换算坑**（详见下节）：同一个判据换 ctx 结论就变，害我连点空两次。

### ⚠️ 附带发现的项目级隐患：`wxreg` 算错了「页面原点」

`wxreg` 用 `dy = outerHeight - innerHeight` 当「顶部偏移」。但小程序渲染层的 `innerHeight`
**已经把底部原生 tabBar 扣掉了**，于是这个差值 = **标题栏 + tabBar**。实测（窗口 1022×810）：

| ctx | 页面 | innerHeight | `outerHeight-innerHeight` | 真实含义 |
|---|---|---|---|---|
| 6 | 首页 | 765 | **45** | ✅ 恰好 = `safeArea.top` |
| 11 | 我的 | 709 | **101** | ❌ 44 标题栏 + **55** tabBar |

**同一判据换个 ctx 结论就变** —— 这就是我连点空两次的原因（点低了 55px）。正确做法：

```python
页面原点 = 窗口原点(screenX/screenY) + 逻辑层 safeArea.top      # 44
```

见 `wxhonor.py` 的 `origin()`。⚠️ **修它会波及所有品牌，要单独一轮 + 全品牌回归。**

## 八、可自动化程度

| 环节 | 是否自动 | 备注 |
|---|---|---|
| 打开荣耀商城小程序 | ✅ | 引擎自动（`open_target.py` 按 appId 复核） |
| 取 cookie（`euid`/`encryptRtNew`/`CSRF-TOKEN`） | ✅ | **CDP 读 cookie jar，不碰界面、不开 H5**；写回后 3 天有效期内直接可读（见第三节） |
| 提 `activityCode` | ✅ | **纯 HTTP 拉 HTML 再正则**，换档期零人工 |
| 查档期 / 签到 | ✅ | **纯 HTTP 两个 POST** |
| 首次绑定账号（微信手机号授权） | ✅ | `wxhonor.py login`；授权是**静默**的，不用收短信 |
| **凭证过期后续期**（每 3 天） | ✅ | **四级自愈**（见第六节）：① **纯 API 登录**（`wx.login` code → 新 cookie，**零 UI**，并写回浏览器）→ ② SSO（打开一次 honor.com 页面借 SID，零弹窗）→ ③ `login`（点「允许」框）→ ④ 报 `NOLOGIN` |
| 连华为账号 SID 都没了 | 🟡 **自动，但不保证** | 才需要第 ③ 级 `login`。属于兜底，实测尚未走到 |
| 风控 / 验证码 | ❌ | 无法自动过 —— 报 `RISK` 并退出，不重试 |

> **一句话**：**日常完全不需要人、也不需要界面**。3 天到期由第 ① 级**纯 API 登录**无声换回
> （连页面都不用开）；只有「连华为账号 SID 都没了」（≈ 从没绑过荣耀账号）才需要第 ③ 级 `login`。
> 真失败也不会静默 —— 报 `NOLOGIN` 并给出命令。

> 实测耗时：**33 秒**（cookie 有效时，含引擎侧投递脚本 + 开小程序的固定开销）；
> cookie 失效、要走第 ① 级自愈时约 **60 秒**。
> 其中与荣耀本身相关的只有「读一次 cookie + 1 个 GET + 2 个 POST」。

## 九、复现步骤

```bash
# ① 容器内（微信实例）—— 只取身份 / 首次授权
python3 wxhonor.py ident      # 读 .honor.com 的 cookie jar → 打印 HONOR_IDENT={...}
python3 wxhonor.py status     # 当前在哪一页 / 隐私弹窗在不在 / 登录态
python3 wxhonor.py login      # 首次：点「点击账号登录」并抓微信原生「允许」框

# ② 宿主机 —— 走引擎（纯 HTTP，自动投递 wxhonor.py 到容器）
WOC_INSTANCE=woc-wx-xxxx python3 wxsign.py honor
```

### 实测输出（2026-09-28）

**任务中心 H5 完全没打开**的情况下（已 `navigateBack` 退掉页面、`Target.getTargets` 里
`jobcenter` 出现次数 = 0）：

```
$ WOC_INSTANCE=woc-wx-2ada0225ca python3 wxsign.py honor
===== 荣耀商城（honor） appid=wx06a8d8c84a18be25 =====
  [ensure] 荣耀商城 → rc=0 (已就绪)
     [honor] appId=wx06a8d8c84a18be25  取自 https://servicewechat.com/wx06a8d8c84a18be25/118/page-frame.html
     [honor] cookie 23 条：euid=有  encryptRtNew=有  CSRF-TOKEN=有  variedData=2141 字节
     [honor] 登录态：已登录 ✅   账号 190****7634 / uid 8550086500223209739
  [ident] 账号 190****7634 / uid 8550086500223209739
  [act] activityCode = QDHDz5SM67T65XPOBMBBQ1
  [info] 档期至 2026-09-30 23:59:59+0800；连续 1/5 天；累计 8 分；今天已签=True
RESULT honor code=415 msg=今日已签到（连续 1/5 天，累计 8 分）

========== 汇总 ==========
  ✅ 荣耀商城 415
成功 1 / 失败 0

总耗时: 32 秒
```

> （这是**当时的原文**。后来加了「写回浏览器」优化，cookie 有效时降到 **33 秒稳定**、
> 且不再需要逻辑层；cookie 失效走第 ① 级自愈时约 60 秒。）

> ⚠️ **上面没有一条是「未签 → `code=0`」的完整记录** —— 原因**不是「恰好已签」**，
> 而是**接入当天我在界面里手工点掉了那次签到**（账号上的 8 积分就是那一下来的）。
> 服务端对签到请求回的是**正确的业务码**（`task.center.today.aready.signin` / numCode 3027），
> 说明参数、认证、链路都对。
>
> ✅ 后来又补了一次**用引擎自己的代码**发签到请求的验证（故意跳过引擎的幂等闸门）：
> `honor_ident()` → `honor_activity_code()` → `honor_post("/tdcs/taskcenter/taskCenterSignIn", …)`，
> 服务端仍回 `3027 今日已签` —— 证明引擎**实际构造出来的 body / header / cookie** 被服务端
> 当作**合法签到请求**接受（不是参数错、不是 `user.not.login`、不是风控）。缺的只剩
> 「服务端处于未签态」这**一个外部条件**。
>
> → **首次真签到成功后请回来把这一段补上**，并把 `brands.json` 里该条目的 `verified` 改成 `true`。
