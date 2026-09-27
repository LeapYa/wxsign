# 奈雪点单（pin-dao.cn / 品道自研）后端 · 接口实测记录

> 首次打通 2026-09-27。**第五个后端**。「奈雪币」每日签到。
> 结论全部来自**解包 `wxab7430e6e8b9a4ab`**（`wxapkg.py`，不用打开小程序），不是靠点界面试出来的。

## 一、结论（先看这段）

- 后端是**品道自研**（`pin-dao.cn`），**不是企迈、签到也不在 H5**。
  包里的 `qmai.cn/naixue-login` / `bvrada-user` / `bvrada-promote` 只承担登录/会员的一部分 ——
  **签到走的是 `https://tm-api.pin-dao.cn`**。
- 签到页是**原生页** `pkgBasics/pages/signInReminder/signInReminder`：
  它的 `onShow` 里 `getApp().silentLogin()` 之后直接调 `postSignSave()`
  —— **一进这个页面就自动签到**，再把返回的 info 渲染成「签到成功」页。
  所以**只要接口能调通，整条链路就成立**，不需要驱动任何 UI。
- 认证只要一个头：`Authorization: Bearer <accessToken>`（JWT，iss=`pd-passport`，**有效期 120 天**）。
- 会话直接在 `getApp().globalData.accessToken` 里 —— 小程序 `silentLogin()` 用
  `wx.login` 的 code 换来，我们**不碰 code**，只读结果（与 OPPO 读 NEWOPPOSID 同一思路）。
- 该接口**幂等**：同一天连调两次都回 `code=0`，**不会重复发币**。所以**不需要「先查后签」**。

## 二、归属与识别

| 项 | 值 |
|---|---|
| 小程序 | `奈雪点单`，appId `wxab7430e6e8b9a4ab` |
| API 主机 | `https://tm-api.pin-dao.cn`（解包 env 配置：`prod:{api:"…", h5:"https://tm-web.pin-dao.cn"}`） |
| H5 主机 | `https://tm-web.pin-dao.cn`（营销活动用，**签到不经它**） |
| 签到页 | `pkgBasics/pages/signInReminder/signInReminder`（原生页，导航栏标题「签到提醒」，页面内容是「签到成功」） |
| 入口 | 「我的」页右上角绿色**「每日签到」**按钮 |
| 品牌常量 | `brand=26000252`、`businessType=1`、`loginType=3`、`version=6.0.84` |
| 页面栈（实测） | `pages/user/index > pages/webView/webView > …signInReminder` |

## 三、认证

```http
POST /user/sign/save HTTP/1.1
Host: tm-api.pin-dao.cn
Content-Type: application/json
Authorization: Bearer eyJhbGciOiJIUzI1NiJ9…   ← getApp().globalData.accessToken
storeId:                                        ← 可空
iv: bEZd3soOfZvFptks                            ← 固定 IV（原码默认值）

{"common":{…},"params":{"signDate":"2026-9-27"}}
```

⚠️ 无 token 调业务接口会回 **`code=1500000 "用户未登录，请登录！"`** —— 而不是 HTTP 401。
所以**判失败要看 body 的 `code`，不要看 HTTP 状态码**（HTTP 一直是 200）。

## 四、完整链路（每步都实测）

```
① 取会话（wxnaixue.py，容器内跑）
     getApp().globalData.accessToken  →  Bearer
        ↓
② 签到
     POST /user/sign/save   params={signDate:"2026-9-27"}
        ↓  code=0 即成功
③ 佐证（可选）
     POST /user/memberCenter/userAsset  →  data.coin（奈雪币余额）
```

### 登录（首次 / token 过期时才需要，日常用不到）

```
POST /passport/authenticate/wxapp/verify/grc
     params={type:3, wxappCode:<wx.login() 的 code>, …}
  →  data={accessToken, openId, unionId, firstLogin}
```

`type:3` 就是原码配置里的 `loginType:3`。

## 五、请求体是**两层**：`{common, params}`

这是本后端最容易踩的地方 —— **业务入参不是扁平的**：

```json
{
  "common": {
    "platform": "wxapp", "version": "6.0.84", "imei": "",
    "osn": "", "sv": "", "lat": "", "lng": "", "lang": "zh_CN",
    "currency": "CNY", "timeZone": "",
    "nonce": 123456, "openId": "QL6ZOftGzbziPlZwfiXM",
    "timestamp": 1790512693, "signature": "…Base64(HmacSHA1)…"
  },
  "params": {
    "businessType": 1, "brand": 26000252, "tenantId": 1, "channel": 2,
    "stallType": null, "storeId": "", "storeType": "", "cityId": "",
    "districtId": "", "appId": "wxab7430e6e8b9a4ab", "dAId": "",
    "signDate": "2026-9-27"
  }
}
```

- **网关强校验 `common`** —— 尤其 `nonce`。只传 `{platform, version}` 会被直接拒：
  ```json
  {"error_msg":"invalid request body, no nonce"}
  ```
  （注意这是**网关**的错，格式与业务的 `{code,message}` 不同。）
- `signature` 的算法（照抄原码 `l()`）：
  ```
  raw = "nonce=<nonce>&openId=<openId>&timestamp=<timestamp>"
  signature = Base64( HmacSHA1(raw, "sArMTldQ9tqU19XIRDMWz7BO5WaeBnrezA") )
  ```
- ⚠️ `common.openId` 的取值是原码里的**硬编码常量** `QL6ZOftGzbziPlZwfiXM`，
  **不是**当前用户的真 openId（真用户身份在 token 里）。照抄即可，别"修好"它。

## 六、踩坑与误判纠正

### 1. `signDate` **不补零**

原码：

```js
var t = new Date();
var o = t.getFullYear() + "-" + (t.getMonth() + 1) + "-" + t.getDate();
// → "2026-9-27"，不是 "2026-09-27"
```

补零了服务端认不认**没有实测**，但**没必要冒险** —— 照抄原格式。

### 2. 「奈雪是企迈」是错的

`wxapkg.py feat` 会报 `域名特征: qmai.cn`（因为包里确实有 `qmai.cn/naixue-login` 等），
但那是**登录/会员**那一小块复用了企迈；**签到是自研**（`tm-api.pin-dao.cn`）。
→ 教训：`feat` 的域名命中只说明「包里出现过」，**不等于「签到走这条线」**，
  要 grep 到签到接口本身才能定论。

### 3. 「奈雪的茶商城」是另一个小程序、而且是有赞

搜索「奈雪」会出 4 个：`奈雪点单` / `奈雪的茶商城` / `奈雪茶院` / `奈雪的茶合伙人`。
其中 `奈雪的茶商城`（`wxe611cd893c49d73e`）是**有赞**（`wscump/checkin/checkinV2.json`），
和点单小程序**不是一套体系**。本引擎接的是**奈雪点单**。

## 七、幂等与验证（2026-09-27 实测）

| 实验 | 结果 |
|---|---|
| 带 token 调 `/user/sign/save` | `{"code":0,"success":true,"data":{"flag":false,"infos":[…3 条说明…]}}` |
| **再**调一次（同一天） | 返回**完全相同** → 幂等，不重复发币 |
| 不带 token 调 | `{"code":1500000,"message":"用户未登录，请登录！"}` → 严格按 token 认人 |
| 只传 `{platform,version}` 的 common | 网关拒：`invalid request body, no nonce` |
| 查 `/user/memberCenter/userAsset` | `data.coin = "1"` ← 正是「每天签到可获得 1 奈雪币」 |

**旁证**：签到成功页（截图）显示的三条说明文案，与接口返回的 `data.infos` **逐字一致**
（「每天签到可获得 1 奈雪币」「连签 7 天额外得 9 奈雪币」「可兑奈雪券及周边好礼」）——
证明页面数据就是来自 `/user/sign/save`，链路对应无误。

## 八、怎么挖出来的（复现步骤）

```bash
# 1. 解包判厂商 + 有没有签到（不用打开小程序）
python3 wxapkg.py feat wxab7430e6e8b9a4ab
#   → 域名特征: qmai.cn；签到命中: 签到, 每日签到, signIn

# 2. 列出所有请求域名 —— 这一步才看清真正干活的不是企迈
python3 wxapkg.py grep wxab7430e6e8b9a4ab "https?://[a-zA-Z0-9.\-]+"
#   → pin-dao.cn 系（tm-web / tm-api / trade-marketing-prod-oss）才是主体

# 3. 找签到接口（关键：grep「立即签到」零命中 → 说明不是靠按钮，是 onShow 自动调）
python3 wxapkg.py grep wxab7430e6e8b9a4ab "签到" --ctx 150
#   → 命中 pkgBasics/pages/signInReminder/…

# 4. 挖 apiMap（所有接口路径都在一个对象里，一次就能拿全）
python3 wxapkg.py grep wxab7430e6e8b9a4ab "postSignSave" --ctx 800
#   → postSignSave:"/user/sign/save" + 页面 onShow→postSignSave() 的完整实现

# 5. 挖请求封装（base 拼接 / 头 / 签名）
python3 wxapkg.py grep wxab7430e6e8b9a4ab "bEZd3soOfZvFptks" --ctx 1500
#   → common/params 两层结构 + Authorization 头 + HmacSHA1 签名函数

# 6. 挖品牌常量
python3 wxapkg.py grep wxab7430e6e8b9a4ab "businessType" --ctx 500
#   → r({businessType:1, brand:26000252, …}) r({loginType:3, …})
```

## 九、代码落点

| 文件 | 作用 |
|---|---|
| `wxnaixue.py` | （容器内跑）读 `getApp().globalData.accessToken`，输出 `NX_JSON=…` |
| `wxsign.py` → `do_sign_naixue()` | 引擎侧：取会话 → POST `/user/sign/save` → 查余额佐证 |
| `wxsign.py` → `NX_*` 常量 | base / appid / brand / version / iv / salt / 假 openId |
| `brands.json` → `"engine": "pindao"` | 分派到上面的实现 |
