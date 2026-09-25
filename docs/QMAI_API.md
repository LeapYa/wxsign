# 企迈（qmai）后端接口逆向与实测记录

> **结论：能做，而且呷哺呷哺已经真机跑通**（2026-09-26）。
>
> 本文档同时记录了**我在这条线上犯过的一串错误**，因为那些坑比结论更值得记 ——
> 我先后下过三次「这后端做不了 / 商户没开活动」的结论，**三次全错**。

---

## 一、结论

| 项 | 结果 |
|---|---|
| 接口链路 | ✅ 完全打通 |
| 适配器 | ✅ `wxqm.py` + `wxsign.py::do_sign_qm`（engine `qmai`） |
| **呷哺呷哺**（storeId `214176`） | ✅ **真机签到成功**，界面弹「签到成功！恭喜获得 1 哺币」，积分 0→1、连签 0→1 天 |
| 引擎端到端 | ✅ `RESULT xiabuxiabu code=415 msg=该用户今日已签到`（重跑幂等正确） |
| 李先生牛肉面大王（storeId `49112`） | ⚠️ 未验证（缺它的 `activityId`，且它属于另一业务线 `catering`） |

---

## 二、真正的接口（`cmk-center/sign/*`）

**这是本轮的转折点**：包里其实有**两套**签到接口，我一开始挖到的是**错的那套**。

| | 错的（积分商城那套） | ✅ **对的（签到有礼活动）** |
|---|---|---|
| 路径 | `/web/<biz>/integral/sign/*` | **`/web/cmk-center/sign/*`** |
| 页面 | `subpackages/sign-in/index/index` | **`pluginMarketing/checkin/index/index`** |
| 未开通时 | `detail` 回 **`400042 商家未开启此功能`** | `activityInfo` 回 `status:true` |
| 是否真的能用 | ❌ 打不通（页面是空的） | ✅ **能签** |

> ⚠️ **`400042` 是个误导性错误码** —— 它让人以为「商户没开功能」。
> 实际上对呷哺/李先生，**那套接口本来就是另一条产品线**，永远回这个错。
> 我据此连着下了三次错误结论。

### 接口清单

```
POST /web/cmk-center/sign/activityInfo            活动信息（activityStatus / serverTime / 规则）
POST /web/cmk-center/sign/userSignStatistics      我的统计（积分 / 连签 / 下次奖励）
POST /web/cmk-center/sign/userSignRecordCalendar  签到日历
POST /web/cmk-center/sign/takePartInSign          ★ 签到
POST /web/cmk-center/sign/userReward              我的奖品
POST /web/cmk-center/sign/tomorrowNotice          明日提醒
POST /web/cmk-center/common/getCrmAvailablePoints 可用积分
```

**请求体就两个字段**（源码 `takePartInSign({activityId, storeId})`）：

```json
{"activityId": "1212715500988841985", "storeId": "214176"}
```

### 请求头

```
Content-Type: application/json
Accept: v=1.0
Qm-From: wechat
Qm-From-Type: <biz>          ← 业务线（呷哺 mealmate / 餐饮大盘 catering）
store-id: <商户号>
Qm-User-Token: <会话>
scene: 1101
```

⚠️ **`cmk-center` 不带业务线前缀**：写 `/web/mealmate-apiserver/cmk-center/...` 会回
`43004 http状态码异常`（"路径不存在"的另一种伪装）。业务线只在 `Qm-From-Type` 里。

### 实测响应

```json
// takePartInSign（今天已签时）
{"code":0,"data":{"isPassCheck":false},"message":"该用户今日已签到","status":false}

// userSignStatistics
{"code":0,"data":{"basicPoints":1,"nextSignDays":6,
 "nextRewardList":[{"signNum":7,"rewardList":[{"rewardName":"签到赠冰淇淋"}]}]}}

// activityInfo
{"status":true,"data":{"activityStatus":4,"id":"1212715500988841985",
 "shareTitle":"签到领好礼","serverTime":"2026-09-26 02:18:02"}}
```

---

## 三、两个必须知道的前提

### 1. `activityId` 是**商户级活动 ID**，必须配

它由服务端随「我的 →每日签到」入口下发，**包里没有**，所以写进 `brands.json` 的
`qm_activity`。

**取法**：打开小程序 → 进「我的」→ 点「**每日签到**」→ 读签到页 data 的 `activityId`
（或抓一次 `userSignStatistics` 的请求体）。

### 2. 必须**已登录（绑手机号）**

签到页源码：

```js
onClickCheckin() {
  if (this.isDisabledCheckin) return;
  if (!c.a.state.userMobile) return void this.popAuthorization();   // ← 没手机号就弹授权
  ...
  await takePartInSign({activityId: this.activityId, storeId: c.a.state.brandId});
}
```

未登录时：**页面根本不发请求**；接口层回 `100005 用户未登录`。
所以本后端**首次需要人工过一次手机号授权**（授权后会话在内存里，重启小程序就没了）。

**怎么过**：在签到页点「立即签到」→ 弹「欢迎加入…」→ 勾选同意 → 点「手机号一键登录」
→ 微信原生授权框点「允许」。

---

## 四、身份从哪来（`wxqm.py` 的双路设计）

| 路线 | 适用 | 做法 |
|---|---|---|
| ① **storage** | **会持久化**登录态的商户（实测：李先生） | 读 `loginData` = `{token, store:{id,name}, user:{eOpenId,eMobile}}` |
| ② **抓真实请求头** | **不持久化**的商户（实测：呷哺 —— `loginData` 是空串） | 开 CDP `Network.enable` → 触发一次重载 → 从请求头读 `Qm-User-Token` + `store-id`（顺带读到 `Qm-From-Type`，连 `qm_biz` 都不用配） |

**为什么必须双路**：企迈是**静默登录** —— 小程序打开时自己换一次会话。
有些商户把它写进 storage，有些不写（token 只活在内存、每个请求现取现用）。
**只读 storage 会在呷哺上永远拿到空。**

---

## 五、我在这条线上犯过的错（按时间顺序）

| # | 我的结论 | 真相 |
|---|---|---|
| 1 | 「企迈有三层防自动化（AES-GCM + X-Qm-Sign + 风控），不做」 | AES-GCM 只是打包进来的库、且可旁路/降级；**没有实际签名要求** |
| 2 | 「网关不认我们的请求形态」 | 只是 URL 拼错 |
| 3 | 「被阿里云 WAF 挡住，环境不可做」（还为此写了 WAF 求解器、换宿主机重测） | **路径漏了 `/web` 前缀** —— WAF 对「不存在的路由」甩 110310 字节的 JS 挑战页，我把「接口不存在」当成了「被墙」 |
| 4 | 「两家商户都没开签到活动（400042）」 | 我用的是**错的那套接口**（`integral/sign`）+ **错的页面**（`subpackages/sign-in`） |
| 5 | 「必须找一个开着活动的企迈商户才能验证」 | 呷哺本来就能签 —— **用户一句「呷哺呷哺是不是能签到」把我拉回正轨** |

**共同病根**：**每次都把「我自己搞错了」包装成「对方有防御/没开功能」**。
每一层都"看起来很合理"，所以没有回头质疑前提。

**真正救回来的那一步**：**用 CDP 抓「小程序自己发的请求」**（`survey/host_net.py`）——
判「服务端到底怎么响应」这类问题，这是唯一硬判据；自己复刻的请求只能回答
「我构造得对不对」。

---

## 六、踩过的技术坑

1. ⚠️ **JS 层拦不到企迈的请求**：它自己在模块加载期就捕获了 `wx.request` 引用
   （`n || (n = e[t])`），事后替换无效；SDK 实例藏在模块闭包里也扫不到。
   → 用 **CDP Network domain** 抓。
2. ⚠️ **本机代理会返回别的请求的缓存**：第一次打 `webapi.qmai.cn`，响应头里竟是
   `Access-Control-Allow-Origin: http://zhyx.eingdong.com` 和易东的 cookie ——
   那是之前易东请求的缓存。**curl 一律带 `?t=<ts>` + `Cache-Control: no-cache`。**
3. ⚠️ **「空响应」有多种含义**：`content-length: 0` 可能是路径不对、可能是被挑战、
   可能是没带 token —— 别只看长度。
4. ⚠️ **`onClickCheckin()` 无参调用**：已签到时它会提前 return（不发请求），
   所以「调了没反应」≠「接口不通」。
5. ⚠️ **企迈的隐私页可以直接调方法**：`confirm()` / `checkboxChange()` /
   `handleAgreePrivacyAuthorization()` —— 比猜「同意」按钮坐标快且不会点错。

---

## 七、相关工具（`survey/`）

| 文件 | 用途 |
|---|---|
| `host_net.py` | **CDP 抓小程序真实请求（含响应体）** —— 揭穿整件事的就是它 |
| `host_eval.py` | CDP 在小程序逻辑层执行任意 JS |
| `waf/` | 早期 WAF 实验存档（**已证明是弯路**，留作反例） |
