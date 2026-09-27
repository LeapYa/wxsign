# 蜜雪冰城（mxbc）· 每日抽奖 —— **纯 HTTP 已跑通**

> 2026-09-27 首次跑通。链路：蜜雪 accessToken → 兑吧免密登录 → **Node eval JS-challenge 算 token** → 抽奖。

## 一、结论

- 用户说的**「每日抽奖」确实存在**，位置在**会员权益**：
  「我的 → 会员权益」第 3 条 = **「雪王币抽奖」**（`buttonText:"立即抽奖"`，
  `activityType:1013`、`activityId:1963852076723355650`）。
- 活动规则原文（`projectRule.query`）确认「每天 1 次免费」：
  > 用户在活动期间**每天有 1 次免费抽奖的次数**，免费次数用完后，可消耗 **20 雪王币/次**继续参与抽奖
- 承载页是**第三方（兑吧 duiba）的大转盘 H5**，**不在蜜雪小程序包里**：
  `https://76177-activity.dexfu.cn/galaxy/app/project/2924/index.html`
- **✅ 整条链路已用纯 HTTP 跑通**（含那个 JS-challenge token），脚本：`mx_lottery.py` + `wxmx.py`。

```
[info] 剩余免费=1 已抽=0 雪王币=0
[chal] token=g0831721…（8 字符）
[draw] code=000000 → optionName="谢谢参与"   ← 当天免费抽一次的结果
[info] 抽后：剩余免费=0 已抽=1
```

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
| 平台 | **兑吧（duiba）** spark，网关 `x-service-id: PROJECTX-GATEWAY` |

> 首页活动区的「右二-抽雪王手办」也是同一个 URL（只是另一个入口位）。

## 三、身份链路（已实测）

```
① 蜜雪 accessToken —— 容器内 CDP 读 getApp().globalData.accessToken（JWT，195 字符）
② GET  /v1/duiba/getLoginUrl?dbredirect=<活动URL>       （蜜雪 API，需签名）
     → data.loginUrl = <活动域>/autoLogin/autologin?…&uid=…&credits=0
                       &sign=<32hex>&appKey=3AkRudni3MSyvsh89f745gEiPoYb&vip=1&timestamp=…
③ 访问 loginUrl → 拿 dexfu 会话 cookie
     JSESSIONID / wdata3 / wdata4 / tokenId / dcustom / isNotLoginUser=false   ✅
```

⚠️ 小程序 `navigate()` 对 `http(s)://` 只开 web-view，**默认不注入 token**；
只有 `needToken` 的 URL 才注入。dexfu 走的是另一支：
`h = url.indexOf("dexfu.cn") !== -1` → `getDBLoginUrl(u)` → 打开返回的 `loginUrl`。

## 四、接口（兑吧侧）

前缀 = `/galaxy/app/project/2924/`

| 接口 | 作用 | 结果 |
|---|---|---|
| `luck/index.do` | 抽奖首页信息 | ✅ **不需要 token** |
| `luck/draw.do` | **抽奖**（`withToken`） | ✅ 带 token 后成功 |
| `luck/deductCredits.do` | 花币抽（20 雪王币） | 需 token |
| `luck/queryStatus.do` | 查询状态（`withToken`） | 需 token |
| `projectRule.query` | 活动规则 | ✅ |
| `getTokenKey.query?projectId=2924` | **JS-challenge：key** | ✅ 返回混淆 JS（≈2.8KB） |
| `getToken.query?projectId=2924` | **JS-challenge：token** | ✅ 返回混淆 JS（≈17KB） |

`luck/index.do` 返回：

```json
{"success":true,"code":"000000","data":{
  "credits":0, "preConsumeCredits":20, "hadPlayTime":0,
  "freeTimeNum":1, "freeTimeUnit":1, "remainFreeTimes":1, "notLogin":false,
  "rewardList":[{"id":34962,"name":"88雪王币"},…]}}
```

## 五、⭐ JS-challenge 与解法

`luck/draw.do` 的参数里要带一个 `token`。它不是签名，而是**服务端下发代码、前端现场算出来**的：

```js
// vendors.js（兑吧公共库）
function p(js){                       // 执行服务端下发的 JS
  var n = document.createElement("script");
  n.innerHTML = js;  document.head.appendChild(n);
  setTimeout(function(){ document.head.removeChild(n) }, 1);
}
function E(){ return d("getTokenKey.query") }   // 先取 key 段
function S(){
  var e = await d("getToken.query");
  if (e.success) { p(e.data);                    // ← 执行第二段
                   return window.aWlgXsMotnsZuy(); }   // ← 取 token
}
```

服务端返回的两段 JS 都长这样（**每次不同**：变量名随机、key 数组随机、到处插无用注释）：

```js
var __ZkUvL5WU = String.fromCharCode;              // \u0053\u0074… 转义混淆
var _x_duh = [3314,1923,885,4011,3509];            // 随机 key
var _$pnX = function(){ return arguments[0] ^ _x_duh[0] };   // 一堆 XOR 解码函数
eval(__ZkUvL5WU(32) + __ZkUvL5WU(…计算出的字符码…) + …);    // 拼代码再 eval
```

⚠️ **关键**：**`aWlgXsMotnsZuy` 这个名字在文件里 grep 不到** ——
它是在 `eval(String.fromCharCode(...))` 拼出的那段代码里才被定义到 `window` 上的。
所以定位它只能靠「**执行后 `window` 上新增了哪个函数**」。

**解法（已实现）**：在 **Node 的 `vm` 沙箱**里跑这两段 JS，
mock 掉 `document`（`createElement`/`head.appendChild` 直接执行 script 内容）、
`location`、`navigator`、`screen`、`localStorage` 等浏览器对象，
然后对比执行前后沙箱新增的 key，取那个新出现的函数调用即得 token。

实现见 `mx_lottery.py` 里的 `NODE_RUNNER`（约 40 行 JS）。

## 六、实测结果（2026-09-27）

| 步骤 | 结果 |
|---|---|
| 会话链路 | ✅ 免密登录成功（`isNotLoginUser=false`） |
| `luck/index.do` | ✅ `remainFreeTimes:1`、`hadPlayTime:0`、`credits:0` |
| Node eval token | ✅ 得到 8 字符 token（如 `g0831721`） |
| `luck/draw.do` | ✅ `code=000000`、`optionName:"谢谢参与"`、`remainFreeTimes:0`、`hadPlayTime:1` |
| 抽后复查 | ✅ `remainFreeTimes=0 / hadPlayTime=1`（**确实消耗掉了当天的免费次数**） |

副作用确认：抽奖是**真的会消耗次数/发奖**的**写操作** —— 不是"模拟一下"。

## 七、⚠️ 风控与规则（必须知情）

活动规则原文（`projectRule.query`）：

> 任何参与活动的用户不得以任何**机器人软件、爬虫软件**、刷屏软件或任何非人工方式参与活动，
> **一经发现立即取消领奖资格**，且该用户于活动中获得的奖励全部收回

即：技术上做得到，但**规则明确禁止**。是否让它每天自动跑，由使用者自己权衡。

## 八、用法

```bash
# 依赖：容器里要有 wxmx.py（引擎的 ensure_helpers 会自动投递到 /tmp）
#     宿主机 Node 可用（WXSIGN_NODE 指定路径）；蜜雪小程序需处于打开状态（读 token 用）

python3 mx_lottery.py --probe            # 只查状态（不抽奖，安全）
python3 mx_lottery.py --dump-js <dir>    # 导出两段 challenge JS（调试用）
python3 mx_lottery.py --draw             # 走完整链路并抽一次
```

环境变量：`WOC_INSTANCE`（微信容器名）、`WXSIGN_NODE`（node 可执行文件路径）、
`WXSIGN_CTMP`（容器内脚本目录，默认 `/tmp`）。
代理：宿主机需能出网（本项目走 `http_proxy=http://127.0.0.1:10809`，偶发 502 重试即可）。

## 九、蜜雪签名（Python，实测 `/v2/memberRights/mainPage/levels` 等均 code=0）

```python
import hashlib, json, time

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
    p = dict(params); p.pop("sign", None)
    p["appId"] = APPID_WEIXIN; p["t"] = int(time.time() * 1000); p["s"] = 3
    md5hex = hashlib.md5((create_str_before_sign(p) + SALT_WEIXIN).encode()).hexdigest()
    b = bytes.fromhex(md5hex); parts = []
    for i in range(4):                               # 4 组「有符号大端 int」取绝对值
        v = (b[4*i] << 24) | (b[4*i+1] << 16) | (b[4*i+2] << 8) | b[4*i+3]
        if v >= 0x80000000:
            v -= 0x100000000
        parts.append(str(0x7FFFFFFF if v == -0x80000000 else abs(v)))
    return md5hex + "".join(parts), p
```

请求头：`version: 2.8.58` / `Access-Token` / `x-ssos-cid`；
URL 带 `://` 者直用，否则拼 `https://mxsa.mxbc.net/api`（原码 `request/index.js`）。
无 `Access-Token` 调业务接口 → `code=500 未知异常，请联系管理员`（不是 401）。
