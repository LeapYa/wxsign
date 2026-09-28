# 荣耀商城（honor.com）后端 · 接口实测记录

> 首次打通 2026-09-28。这是本项目**唯一一个「签到不在小程序里」**的后端 ——
> 签到本体是**任务中心 H5**，由小程序页面用 web-view 承载。
> 「打开小程序就能签」在这里**不成立**，必须先 navigateTo 那个页面、再 attach 它的 webview。

## 一、结论（先看这段）

- **荣耀商城小程序本身没有签到。** 整包 grep「签到 / 打卡 / 赚积分 / 领积分 / 每日任务 /
  任务中心 / 签到有礼 / 签到提醒」**零命中**（只剩 5 处孤立 i18n 定义，**无使用处**），
  tabBar 只有 `pages/index` / `classify` / `personal`。**别再在小程序里找入口。**
- 签到在**任务中心 H5**（`www.honor.com/cn/msale/mp/jobcenter.html`），
  由小程序页面 `packageActivity/pages/login4Qxmp/login4Qxmp`（标题「任务页面」）承载。
  **H5 是独立 CDP target（`type=webview`）** —— 主 page 的 ctx 列表里**看不见**它，
  必须 `Target.attachToTarget(flatten=True)`。
- API 主机 `https://openapi-cn.c.honor.com`。**没有签名、没有 nonce**。
- 认证是**页面自己的 cookie**（`.honor.com`：`euid` / `encryptRtNew` / `CSRF-TOKEN`）——
  所以最省事的做法是**在 H5 页面内发 `fetch(credentials:'include')`**，
  认证交给浏览器，**一个 cookie 都不碰**。
- **`activityCode` 每期会变，但零人工**：它是页面 HTML 里内联的组件属性
  （`.sign-in-style4[data-activity-code]`），现场读 —— 所以 `brands.json` 里**不用配**。
- 幂等判据用 `queryTaskCenterInfo` 的 `result.signInInfo.signInToday`（服务端背书），
  且服务端对重复签到也回专门的码 `task.center.today.aready.signin`（双保险）。

## 二、归属与识别

| 项 | 值 |
|---|---|
| 小程序 | `荣耀商城`，appId `wx06a8d8c84a18be25` |
| 承载签到的页面 | `packageActivity/pages/login4Qxmp/login4Qxmp`（标题「任务页面」） |
| 承载签到的 H5 | `https://www.honor.com/cn/msale/mp/jobcenter.html?version=9` |
| API 主机 | `https://openapi-cn.c.honor.com`（= H5 的 `window.pageConfig.openapiDomain`） |
| 埋点主机 | `https://dap-cn.c.hihonor.com`（与业务无关，可忽略） |
| 签名/风控 | 无签名；有极验 `geetest`（**签到链路不经过它**，见第四节） |
| 档期（2026-09-28 实测） | `activityCode = QDHDz5SM67T65XPOBMBBQ1`，`2026-09-30 23:59:59+0800` 结束 |
| 签到周期 | **5 天**：`dailyRewards = {1:8, 2:9, 3:11, 4:13, 5:17}` |

> ⚠️ **别把「荣耀商城」和「我的华为」/「华为商城」搞混**：
> 华为商城（`wx208389d90830e5b7`）**没有签到**（详见 README 第七节第 10 条）；
> 微信里也**没有**「我的华为」小程序。荣耀商城是**独立品牌**（honor.com），
> 不是华为的分身 —— 它自己的任务中心 H5 里确实有签到。

## 三、认证：页面自己的 cookie

`.honor.com` 域下的 cookie（实测 dump 出来的全量里挑相关的）：

| cookie | httpOnly | 长度 | 作用 |
|---|---|---|---|
| `euid` | ✅ | 67 | 用户 ID（**关键**） |
| `encryptRtNew` | ❌ | 305 | 加密的 refresh token（**关键**） |
| `CSRF-TOKEN` | ❌ | 42 | CSRF 令牌（**关键**） |
| `uid` / `user` / `name` / `hasphone` | ❌ | 19 / 11 / 11 / 1 | 展示用 |
| `hasLogin` / `hasSigned` | ❌ | 13 / 1 | 前端状态位（`hasSigned=1` = 今天签过） |
| `variedData` | ❌ | 1559 | 设备指纹（**签到请求体里要带**） |

请求头里的 `CsrfToken` 取自 **H5 的 `window.csrftoken`**（由 `openapi-cn.c.honor.com/csrftoken.js`
下发，页面加载时就有了）。

**为什么选「在页面内发 fetch」而不是纯 HTTP**：这些 cookie（尤其 `euid` / `encryptRtNew`）
在 webview 的 cookie jar 里，要搬出来得额外读一次、还要在容器间传凭证。
而在页面内发请求，`credentials:'include'` 让浏览器自己带全 —— 更少变量、更少出错面。
（纯 HTTP 变体存在且可行，只是没必要。）

## 四、完整链路（每步都实测）

```http
# ① 幂等：查任务中心状态
POST https://openapi-cn.c.honor.com/tdcs/taskcenter/queryTaskCenterInfo
Content-Type: application/json
CsrfToken: <window.csrftoken>

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
Content-Type: application/json
CsrfToken: <window.csrftoken>

{"activityCode":"QDHDz5SM67T65XPOBMBBQ1","taskPortal":"4",
 "agent":"<navigator.userAgent>",
 "oas_refer":"https://www.honor.com/",
 "variedData":"<cookie variedData>"}

→ 成功：        {"code":"0","result":{ "earnPoint":8, ... }}
→ 今天已签：    {"code":"task.center.today.aready.signin",
                 "msg":"TASK CENTER TODAY AREADY SIGNIN","numCode":3027,"success":false}
```

> **参数是从 H5 的 `sign_in_interactive.js` 里读出来的**（不是猜的）：
> ```js
> const data = { activityCode, taskPortal, agent: window.navigator.userAgent,
>                oas_refer: window.location.origin + '/',
>                variedData: utils.cookie.get('variedData') ? utils.cookie.get('variedData') : null }
> const { code, result } = await request('POST', '/tdcs/taskcenter/taskCenterSignIn', data, headers)
> ```
> 注意它的 `headers` 只有 `Content-Type` —— `CsrfToken` 是全局 `request()` 封装自动加的。

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
> 我在挖签到的时候先在包里 grep 到了它，一度以为这就是签到接口。
> **它不是** —— 它是「做任务」的（浏览商详页 / 分享 / 小游戏）。
> 真正的签到在 H5 里，接口是 `/tdcs/taskcenter/taskCenterSignIn`。
> （顺带：那里面 `taskCenterId` 的实际取值就是 `activityCode` —— 见 `sign_in_interactive.js` 里
> `` link += `&taskId=${taskCode}&taskListCode=${taskListCode}&subTaskCode…&taskCenterId=${activityCode}` ``。）

## 五、`activityCode` 每期会换 —— 但零人工

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

所以取它的判据是 **`.sign-in-style4[data-activity-code]`** —— 属性名稳定、值随档期变，
现场读即可。**不需要配在 `brands.json` 里**（这点比 OPPO 的 `oppo_activity` 还省事，
那边还得写一次发现逻辑，这边 H5 自己就带着）。

> ⚠️ 注意 `.sign-in-style4` 的 `.main-btn` **在 HTML 里写死文字是「签到」**，
> 但 JS 在**已签状态**下会把它改成「领更多积分」—— 所以**别按文案判断今天签没签**，
> 要用 `queryTaskCenterInfo` 的 `signInToday`（服务端背书）。同理 `.continuous-days`
> 那个 `SPAN`（「已签到 1 天」）也是运行时渲染的，别拿它当判据。

## 六、首次登录（`wxhonor.py login`）

**微信手机号授权 = 自动注册 / 登录荣耀账号**（包内文案原文）：

> 「点击同意后，我们将申请获取您的**微信手机号**用于绑定登录或注册荣耀账号」

比华为商城好一截 —— **不需要手工填手机号 + 短信码 + 密码**。绑定关系在服务端、永久有效，
所以这一步**一辈子一次**，之后日常签到全自动。

链路：`我的` 页 → 点「点击账号登录」 → 弹**微信原生**手机号授权框 → 点「允许」。

全自动、**零硬编码坐标**：

| 步骤 | 判据 |
|---|---|
| 隐私声明「同意」 | 类名 **`.confirm_btn`**（外层 `AuthorizeModal-*`） |
| 登录入口 | 类名 `[class*=u-login]` + 文案「点击账号登录 / 立即登录 / 去登录」 |
| 微信原生「允许」框 | **像素判据**（逐行扫绿色、按 x 中心聚类、宽 100~260 高 28~80） |
| 页面坐标 → 屏幕坐标 | **窗口原点 + 逻辑层 `safeArea.top`**（⚠️ 不是 `outerHeight-innerHeight`，见第七节） |

> ⚠️ 两个实操注意：
> 1. 那个微信原生「允许」框**会自己超时消失**（实测约半分钟），而且点完登录入口后
>    **不是立刻弹**（有 1~2 秒延迟）—— 所以脚本是「点登录 → 每 2s 轮询抓框」一气呵成，
>    **别在中间插别的命令**。
> 2. 首次没点掉、又短时间重试，会**疑似被限流**（点登录入口不弹框），隔几分钟即恢复。

## 七、我在这条线上犯过的错（按时间顺序）

1. **以为荣耀 = 华为的小号，先去啃了华为商城。** 结论：华为商城小程序**根本没有签到**
   （README 第七节第 10 条列了四条证据）。白花了一轮。
   → 教训：**先确认这个小程序「有没有签到」，再谈怎么签**。判据是包内 grep + 登录后实地翻菜单，
   别信搜索引擎的「华为商城小程序有每日签到」（那说的是 App）。
2. **以为签到在「我的」菜单里。** 翻遍了（订单 / 拼团 / 售后 / 优惠券 / 积分 / 地址 / 消息），
   只有「我的积分」→ 会员积分页 → 「积分规则」H5 里写着签到在别处。
   → 真入口是小程序**首页中间那块的「全部任务 / 赚积分」**，或者**直接 navigateTo 任务中心页**
   （`/packageActivity/pages/login4Qxmp/login4Qxmp`）—— 后者更可靠，也不用找按钮。
3. **以为签到请求走小程序逻辑层的 `wx.request`。** 装了 `wxnet.py` 去拦，**一条都没拦到**。
   → 因为**签到在 H5 里发**。小程序侧那些 `/ams/taskcenter/completeTask` 是**任务**用的。
   这是全篇最关键的一次纠偏。
4. **按「元素最多的 ctx」找 H5**（`find_dom_ctx`）→ 必然选到残留的搜索页。
   → 正解：**先说要找什么**（`find_ctx_with([...])`），再看哪个 ctx 有。这个坑 README 第六节也写了，
   我还是又踩了一次 —— 因为它伪装成「H5 内容不在 DOM 里」。
5. **`wxreg` 的坐标换算坑**（详见下节）：同一个判据换 ctx 结论就变，害我连点空两次，
   一度以为是按钮在视口外。
6. **面板彻底打不开**（点侧边栏「小程序」毫无反应，WMPF 进程健康）。
   → 发现 `open_via_souyisou` 其实把**搜一搜结果页**开出来了，只是被主窗口压着 ——
   `xdotool windowmove <主窗口> 0 1080` 把它移出屏 → 结果页露出来 → 点第一张卡片 →
   小程序就打开了（CDP 复核 appId 一致）。**不用重启微信。**

### ⚠️ 附带发现的项目级隐患：`wxreg` 算错了「页面原点」

`wxreg` 用 `dy = outerHeight - innerHeight` 当「顶部偏移」。但小程序渲染层的 `innerHeight`
**已经把底部原生 tabBar 扣掉了**，于是这个差值 = **标题栏 + tabBar**。实测（窗口 1022×810）：

| ctx | 页面 | innerHeight | `outerHeight-innerHeight` | 真实含义 |
|---|---|---|---|---|
| 6 | 首页 | 765 | **45** | ✅ 恰好 = `safeArea.top` |
| 11 | 我的 | 709 | **101** | ❌ 44 标题栏 + **55** tabBar |

**同一判据换个 ctx 结论就变** —— 这就是我连点空两次的原因（点低了 55px）。
正确做法：

```python
页面原点 = 窗口原点(screenX/screenY) + 逻辑层 safeArea.top      # 44
```

见 `wxhonor.py` 的 `origin()`。⚠️ **修它会波及所有品牌，要单独一轮 + 全品牌回归。**

## 八、可自动化程度

| 环节 | 是否自动 |
|---|---|
| 打开荣耀商城小程序 | ✅ 引擎自动（`open_target.py` 按 appId 复核） |
| 跳到任务中心页 | ✅ 逻辑层 `wx.navigateTo`（而且**不用点任何按钮**） |
| 找到 H5 / attach | ✅ 按 URL 特征找 target + `attachToTarget(flatten)` |
| 取 `activityCode` | ✅ 读 DOM 属性（零人工、每期自动） |
| 首次手机号授权 | ✅ `wxhonor.py login`（**但前提是手机上能收到那次授权 —— 不需要，微信手机号授权是静默的**） |
| 每日签到 | ✅ 全自动（幂等，已签不发请求） |
| 风控 / 验证码 | ❌ 无法自动过 —— 报 `RISK` 并退出，不重试 |

## 九、复现步骤

```bash
# ① 容器内（微信实例）—— 单独看 H5 / 查状态 / 签到
python3 wxhonor.py h5          # 打开（或复用）任务中心 H5，打印它的 webview target
python3 wxhonor.py info        # 只读：activityCode / 档期 / 连续天数 / 今天签没签
python3 wxhonor.py signin      # 真签（幂等：已签直接报 415，不发签到请求）
python3 wxhonor.py signin --json   # 同上，但额外打印机器可读的 HONOR_JSON= 一行（引擎用）

# ② 宿主机 —— 走引擎（它会自动投递 wxhonor.py 到容器）
WOC_INSTANCE=woc-wx-xxxx python3 wxsign.py honor

# ③ 首次登录（每个微信账号一辈子一次）
#    容器内跑；它会点「点击账号登录」并抓微信原生「允许」框
python3 wxhonor.py login
```

### 实测输出（2026-09-28）

```
$ python3 wxhonor.py info
[honor] H5 = https://www.honor.com/cn/msale/mp/jobcenter.html?version=9
[honor] activityCode = QDHDz5SM67T65XPOBMBBQ1
[honor] 档期结束     = 2026-09-30 23:59:59+0800    已连续签到 = 1/5 天
[honor] 累计积分     = 8    今日可得 = 8    今天已签 = True
[honor] → 今日已签到（幂等，不发签到请求）
```

```
$ WOC_INSTANCE=woc-wx-2ada0225ca python3 wxsign.py honor
===== 荣耀商城（honor） appid=wx06a8d8c84a18be25 =====
  [ensure] 荣耀商城 → rc=0 (已就绪)
     [honor] H5 = https://www.honor.com/cn/msale/mp/jobcenter.html?version=9
     [honor] activityCode = QDHDz5SM67T65XPOBMBBQ1
     [honor] 档期结束     = 2026-09-30 23:59:59+0800    已连续签到 = 1/5 天
     [honor] 累计积分     = 8    今日可得 = 8    今天已签 = True
     [honor] → 今日已签到（幂等，不发签到请求）
RESULT honor code=415 msg=今日已签到（连续 1/5 天，累计 8 分）

========== 汇总 ==========
  ✅ 荣耀商城 415
成功 1 / 失败 0
```

> ⚠️ **上面没有一条是「未签 → 成功」的完整记录**：接入当天恰好已签。
> 服务端对签到请求回的是正确的业务码（`task.center.today.aready.signin` / numCode 3027），
> 说明参数、认证、链路都对 —— 但**首次真签到成功后请回来把这一段补上**，
> 并把 `brands.json` 里该条目的 `verified` 改成 `true`。
