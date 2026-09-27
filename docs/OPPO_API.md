# OPPO 商城（opposhop.cn）后端 · 接口实测记录

> 首次打通 2026-09-27。这是本项目**唯一一个非"会员积分"**的后端 ——
> 签到的是 OPPO 官方商城的「每日签到得积分」，不是餐饮会员体系。

## 一、结论（先看这段）

- 认证只要**两个请求头**：`NEWOPPOSID` + `openid`。**没有签名、没有 nonce、没有 cookie 链**。
- 会话由**小程序自己**拿 `wx.login` 的 code 去 OPPO 服务端换 —— 我们不碰 code，
  只读它发业务请求时带出来的头（`wxoppo.py`，从 storage 的 `logininfo` 里取）。
- 首次要**绑手机号**（服务端按 openid 记，**一次性**）。这一步已做成脚本
  `wxoppo_auth.py`，全自动、零人工。
- **`activityId` 按档期滚动**（实测 7 月 → 9 月换过一次），而**旧档期查详情照样回 200** ——
  这是这条线上最容易误判的点。现已做成**自动发现**（见第五节）。
- 幂等判据用 `getSignInDetail.todaySignIn`，**不要**靠"试签一次看错误码"。

## 二、归属与识别

| 项 | 值 |
|---|---|
| 小程序 | `OPPO商城`，appId `wx9c825da1a7ba062e` |
| API 主机 | `https://msec.opposhop.cn`（H5 自己走 `https://hd.opposhop.cn/api`，**实测两者等价**） |
| 承载签到页的 H5 | `https://hd.opposhop.cn/bp/b371ce270f7509f0`（页面 ID **固定**） |
| 入口 | 「我的」页**右上角那个红色图片按钮**（`WX-RIGHT-BTN`，76×34，**无文字**） |
| 签到档期（2026-09-27 实测） | `2026-09-24 ~ 09-30`，本轮 7 天，每天 +2 积分 |

## 三、认证：两个请求头

```http
NEWOPPOSID: eyJpdiI6...           ← storage 里 logininfo.encryptedSession（一个加密信封，328 字符）
openid:     o1yCe4jVJENnJqJxFDWaGmL3kSpw   ← storage 里 logininfo.openId
constToken: VH/NMaGPlolFJQEBVAeIUf0DzQD+EHS4@bj   ← 可选（实测非必需，抓到就一起带）
s_version:  80457
s_channel:  program_wx
source_type: 503
Personalized: 1
Referer:    https://hd.opposhop.cn/bp/b371ce270f7509f0
User-Agent: ...MicroMessenger/7.0.20.1781...MiniProgramEnv/Windows WindowsWechat/WMPF XWEB/20089
```

> ⚠️ **别用「键名含 token 就当 sid」的启发式**。storage 里还有一个 `constToken`，
> 键名带 "token" 会被启发式误取走 → 服务端回 **`403 用户未登录`** ——
> 看起来像"会话过期"，其实是**拿错了值**。认死 `logininfo.encryptedSession`。

## 四、完整链路（每步都实测）

```
① 取会话（wxoppo.py，容器内跑）
     wx.getStorageInfoSync() → 找键 logininfo → { encryptedSession, openId }
        ↓
② 发现 activityId（见第五节；自动，两种方式）
        ↓
③ 查档期 / 幂等
     GET /cn/oapi/marketing/cumulativeSignIn/getSignInDetail?activityId=<id>
        → data: { activityId, taskId, baseAwards[7], signInDayNum, todaySignIn }
        → todaySignIn=true 就是「今天签过了」→ 直接判 415，**不去试签**
        ↓
④ 签到
     POST /cn/oapi/marketing/cumulativeSignIn/signIn   body {"activityId": <id>}
        → code=200 签到成功
```

### 返回码语义

| code | 含义 | 处理 |
|---|---|---|
| `200` | 签到成功 | 成功 |
| `415`（本引擎统一码） | 今日已签到（来自 `todaySignIn=true`，或 signIn 的 `5008`/message 含「已签」） | 视为成功 |
| `5005` | 活动不存在 | **换档期** → 自动重新发现 + 重试一次 |
| `5007` | 活动已经结束 | 同上 |
| `403` + message 含「登录」 | 会话失效 | `NOTOKEN` → 重开小程序让 `wxoppo.py` 重抓 |
| `429` / message 含「验证/风控/频繁/拦截/异常操作」 | 疑似风控 | `RISK`，**不重试**（免得加重） |

> ⚠️ **`5007` 不是"今天签过了"**。老版本没区分，曾把"拿旧档期去签"的失败误读成
> "活动还在、只是今天签过了"。现在幂等判据前移到 `todaySignIn`，两者不再混淆。

## 五、`activityId` 会自动换档期 —— 两条发现路径

`activityId` 是**按档期滚动**的（不像企迈那种商户级固定 ID）。实测换过：
`2071966512754991104`（7 月档期）→ `2094340289534894080`（9 月档期）。

> ⚠️ **旧档期的 `getSignInDetail` 照样回 200**（返回那 7 天的陈旧 `baseAwards`，
> `status` 全 0）—— 极具迷惑性，只有 `signIn` 才回 `5007`。
> 判断档期是否有效**只看 `signIn` 或 `todaySignIn` 所在的那次响应**。

### 主路径：纯 HTTP 拉 H5 的 HTML，解析 SSR 内联 DSL（0.3s）

承载签到页的那个 H5 是 **SSR** —— HTML（约 137KB）里内联了整份活动 DSL，
`cmps` 数组里有 `SignIn_<hash>` 组件，它的配置里**直接写着** activityId：

```
GET https://hd.opposhop.cn/bp/b371ce270f7509f0?appId=…&openId=…&s_version=080457&…
  → HTTP 200
  → "SignIn_82c7796e" → {"type":"SignIn","attr":{…,"activityId":2094340289534894080,…}}
```

**不需要 hook、不需要驱动 UI、不需要打开 H5**（实测 0.3 秒）。

> ⚠️ HTML 里另有 **16 个别的 activityId**（任务 `1919591795180969984`、抽奖、券、
> 积分商品 `1991786826829957218` …），**只有 `SignIn_*` 里那个是签到的**。
> 必须**先按组件名定位**（`"SignIn_[0-9a-fA-F]+"` 或 `"type"\s*:\s*"SignIn"`），
> 再取其后 ~2500 字内的 `"activityId"`；全局瞎抓会得到一堆错候选。
>
> ⚠️ 另有**一个很像的陷阱**：H5 的 Vue data 里 `dsl.activityId = 11510` —— 那是
> **页面 DSL 的 ID**，拿去查签到接口返回 `data` 为空。**两个"activityId"不是一回事。**

### 备选：驱动 H5 + CDP hook 抓它实际发的请求（~40s）

什么时候用：主路径失败时（OPPO 若改成非 SSR，或 HTML 结构变了）。这条更"物理"、
不易被前端改版弄坏。实现在 `wxoppo_act.py`。三个必须知道的点：

1. **H5 是 web-view = 独立 CDP target**（`type=webview`）—— 主 page 的 ctx 列表里
   **没有**它（只有 ctx 1 = 微信浮层、ctx N = 小程序页面），主 page 上 `Network.enable`
   也抓不到它的请求。必须 `Target.attachToTarget({targetId, flatten:True})` 拿 sessionId。
2. **初始请求发生在 attach 之前** → 只 attach 抓不到；而 **`Page.reload` 刷的是整个
   target，等于把 H5 关掉**（webview 变回 `page-frame.html`）。
   ✅ 正确做法：`Page.addScriptToEvaluateOnNewDocument` 注入 XHR/fetch hook +
   在 H5 **内部** `Runtime.evaluate("location.reload()")`。
3. 签到入口是**图片按钮**（无文字）→ 按 innerText 找不到，用 CSS `wx-right-btn`。

### 换档期自愈（两道保险）

- 签前：`oppo_resolve_activity()` = 先纯 HTTP → 失败退 hook → 成功即**回写 `brands.json`**；
- 签中：`signIn` 回 `5005/5007` → **重新发现 + 用新 ID 重试一次**，仍失败才报
  「可能真停办了 / 页面结构已变」。
- `OPPO_NO_DISCOVER=1` 可关掉发现（省请求，直接用配置里的值）。

## 六、首次登录（`wxoppo_auth.py`）

OPPO 要求**绑手机号**才能签到。这一步已全自动：

```
「我的」页 → 点「登录」→ 半屏登录页 → 勾选协议 → 点「手机号快捷登录」
  → 微信原生 getPhoneNumber 授权框 → 点「允许」
  → ✓ 登录成功（「我的」页显示昵称 / 卡券 / 积分）
```

- **已有登录态时会自动识别并跳过**（判据是「我的」页**渲染出的**昵称/积分，
  **不是**"storage 里有没有 sid" —— sid 过期后仍留在 storage 里）；
- 绑定由服务端按 openid 记，**一辈子只需一次**；
- `wxoppo_auth.py` 依赖 `wxqm_auth.py` 的 `find_allow_btn` / `_check_agree`
  （**同一份实现，不重复造**）。

## 七、我在这条线上犯过的错（按时间顺序）

1. **把 `memories` 里的旧 activityId 当"还在生效"** —— 因为 `getSignInDetail` 对旧档期
   回 200。直到拿它去 `signIn` 才看到 `5007`。→ 教训：**判断档期有效性不能只看查详情**。
2. **改错了 `brands.json` 的 appid** —— 曾被一条「`[fix]` 纠正 appId」的日志误导
   （那是 **JWT payload 里后端签发 token 用的 appId**，**不等于微信小程序 appId**）。
   后来用 `wx.getAccountInfoSync().miniProgram.appId` 复核才纠正回来。
   顺带发现「来菜」有**两个同名小程序**（不同商户、openId/mpId 完全不同）。
3. **`find_allow_btn` 把 OPPO 的绿色圆 logo 当成「允许」按钮** —— 判据是"白卡里的绿色横段"，
   而页面背景是白的，logo 正好满足 → 脚本去点 logo。
   修法：用**两帧差异**求"原生层新出现区域的包围盒"，把搜索**锁死在 box 内**，logo 天然被排除。
   （**颜色判据跨品牌必然失效** —— 这是第二次栽在它上面。）
4. **半屏页的页面原点 ≠ 窗口原点** —— 登录页是半屏页（有微信加的「登录」标题栏，61px，
   不在视口里），而页面自报的 `screenY` **仍是窗口原点**，于是坐标**整体偏上 61px**：
   勾选框、登录按钮全点空。修法：`dy = max(0, outerHeight - innerHeight)`。
   （根页面 `innerHeight(779) > outerHeight(776)` → dy 取 0 ✓ 两种形态统一覆盖。）
5. **Escape 关不掉 H5 自带的「订阅提示」** —— 实测画面变化 **0.0%**（Escape 只对
   **微信原生**框有效）。而**按文案点**能关（「我知道了」→ 画面变化 **24.8%**）。
   这个框**一打开小程序就弹**、盖在最上层 → 之后点底部导航/按钮**全部落空**
   （现场像"坐标算错了"）。修法：`wxdom.dismiss_dialogs()` —— 按文案找 → 点 → **画面差异验证**，
   有框继续清、没框一次都不多点。
6. **逐个 ctx `evaluate` 是性能陷阱** —— 不存在的 ctx CDP 根本不回包，每次干等到 socket 超时：
   24 个 ctx 搜一次 ≈ 70 秒，连搜三次 → 250 秒没打出第一行，被外层 timeout 杀且**日志一片空白**，
   现场像"连不上 CDP"，其实是慢死。修法：`wxdom._broadcast` 加 `kind="str"` 模式
   （一口气全发、统一收），实测 2.5 秒。

## 八、可自动化程度

| 动作 | 自动化 | 说明 |
|---|---|---|
| 取会话 | ✅ 全自动 | `wxoppo.py`（storage；不需要抓包） |
| 首次绑手机号 | ✅ 全自动 | `wxoppo_auth.py`；**只需一次**，之后自动跳过 |
| 发现 activityId | ✅ 全自动 | 先纯 HTTP，失败退 CDP hook；成功即回写配置 |
| 查档期 / 幂等 | ✅ 全自动 | `todaySignIn` |
| 签到 | ✅ 全自动 | `signIn` |
| 换档期 | ✅ 全自动 | 自动重新发现 + 重试一次 |

**已知边界**：真出现**行为验证码**时，纯 HTTP **过不了** —— 只能退化到 UI 路径
（驱动 H5，让界面自己处理）。截至目前未遇到过；引擎会把可疑响应明确报成 `RISK` 而不是闷掉。

## 九、复现步骤

```bash
# 1. 打开小程序（引擎会自己开；也可手工用 open_target.py —— 它按 appId 复核，比按标题判可靠）
python3 wxsign.py oppo --ensure

# 2. 全部自动：取会话 → 发现 activityId → 查档期 → 签到
python3 wxsign.py oppo

# 3. 只想看它发现的 activityId / 会话（不签到）时，可以单独跑取会话
docker exec -e DISPLAY=:1 woc-wx-2ada0225ca python3 /tmp/wxoppo.py wx9c825da1a7ba062e 30
#    → OPPO_JSON={..., "sid": "...", "openid": "...", "const_token": "..."}

# 4. 备选发现（驱动 H5 + CDP hook）单独跑
docker exec -e DISPLAY=:1 woc-wx-2ada0225ca python3 /tmp/wxoppo_act.py 120
#    → OPPO_ACT=2094340289534894080
```

### 实测输出（2026-09-27）

```
[ensure] OPPO商城 → rc=0 → 面板搜索 → 打开成功
[oppo] sid=eyJpdiI6Ik…（logininfo，328 字符）constToken=… source=storage
[act]  纯 HTTP 发现 activityId=2094340289534894080
[member] 会话对应账号：昵称013K673G2q3U2q
[info] 签到档期 OK：本轮 1/7 天，今日已签=True
RESULT oppo code=415 msg=今日已签到（todaySignIn=true，本轮 1/7 天）
```
