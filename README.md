# 微信小程序签到合集

微信小程序的每日签到脚本合集，青龙面板 / cron 都能跑。

目前覆盖 **吾享（wuuxiang）** 系 —— 天财商龙旗下的餐饮 SaaS。
辣可可、来菜、九村烤脑花、酒煮江湖用的是它，所以**一套脚本就能全签**。
另有 **5 个非吾享后端**（易东 / 微租林 / 企迈 / **OPPO 商城** / **品道自研**）接口各不相同，
由 `brands.json` 的 `engine` 字段分派 —— 加品牌只改配置，不动代码。

> **上手就三步**：把 `wxsign/` 放进青龙脚本目录 → 配几个环境变量 → 加一条定时任务
> `bash /ql/data/scripts/wxsign/sign.sh`。
>
> **不需要手工抓任何东西，也不需要提供手机号。** 开小程序、刷 token、抓身份
> （`openId / mpId / memberId / 卡号`）、不是会员时注册、签到 —— 全是自动的，
> 第一次跑和以后跑走同一条路。
> 注册默认走**微信手机号授权弹窗**（脚本自己点「允许」），手机号由微信从你自己账号里给；
> 想让注册少走几步可以配 `WXSIGN_REGISTER_PHONE` 改走 API 直连 —— 那是**可选**的。

---

## 一、能签到的小程序（13 个）

| 品牌 | 小程序 | appId | 签到活动 | 后端 | 状态 |
|---|---|---|---|---|---|
| 辣可可 | 辣可可现炒黄牛肉i | `wxf8a17a14c0521576` | 可可会员签到 | 吾享 | ✅ 已跑通 |
| 过桥缘 | 过桥缘游戏中心 | `wx127506c47936059e` | 过桥缘签到送积分 | 吾享 | ✅ 已跑通 |
| 绿茵阁 | 绿茵阁西餐厅Greenery | `wx925a9607ca471de3` | 签到赢好礼：小积分大用处 | 吾享 | ✅ 已跑通 |
| 我的小板凳 | 我的小板凳街坊火锅 | `wx8b134fa76a7a1fdc` | 会员签到赢好礼 | 吾享 | ✅ 已跑通 |
| 来菜 | 来菜 | `wx944ea5f5f7c3dc1f` | 【26】每日签到，半价吃招牌菜 | 吾享 | ✅ 已跑通 |
| 九村烤脑花 | 九村烤脑花YX | `wx24f657cf389aa2ad` | 26年会员签到 | 吾享 | ✅ 已跑通 |
| 酒煮江湖 | 积膳餐饮游戏 | `wx5fb9d9352a88e693` | 酒煮江湖日常积签到活动 | 吾享 | ✅ 已跑通 |
| 刘一手 | 刘一手火锅店 | `wx757e678caa41a2a6` | 每日签到 | **易东** | ✅ 已跑通 |
| 许家去水印 | 许家去水印 | `wx2b979b1c16784d44` | 每日签到（送**去水印次数**） | **微租林** | ✅ 已跑通 |
| 呷哺呷哺 | 呷哺呷哺 | `wx6822e696198e2763` | 签到得好礼（送哺币） | **企迈** | ✅ 已跑通 |
| 李先生牛肉面大王 | 李先生牛肉面大王会员 | `wx9710b09df6cb09a9` | 签到得好礼 | **企迈** | ✅ 已跑通 |
| OPPO 商城 | OPPO商城 | `wx9c825da1a7ba062e` | 每日签到得积分（累计 7 天） | **OPPO 商城** | ✅ 已跑通 |
| 奈雪点单 | 奈雪点单 | `wxab7430e6e8b9a4ab` | 每日签到得奈雪币 | **品道自研** | ✅ 已跑通 |

> 全部已真机跑通（自动开小程序 → 取身份 → 不是会员则**自动注册** → 签到）；
> 返回 `415 今日已签到` 或 `200 签到成功` 都算完成。
> 返回码语义：`200` 成功 / `415` 今日已签 / `406` 不在可签时段 / `401` 还不是会员 / `405` 该租户没活动。

**首次接入**：吾享 / 易东 / 微租林 / **奈雪点单** 什么都不用做；**企迈**与 **OPPO 商城**要过一遍手机号授权
（每个品牌一辈子一次，已做成脚本 —— 见第五节）。

**签到给什么**：一般是**会员积分**（辣可可每天 +1，连签 10 / 20 / 30 天分别加赠 10 / 15 / 20，
积分有效期 365 天）；唯一的例外是**许家去水印**（工具类），给的是**去水印次数**，每天 +1、连签 7 天额外 +5。

> 📖 **单个品牌的细节**都在 `brands.json` 各条目的 `note` 字段里 —— 过桥缘名下还有两个同活动的
> 小程序（签一个就够）、绿茵阁 / 我的小板凳搜索时同族小程序很多（`keyword` 必须用**全名**）、
> 刘一手对应的是**巴塞罗那店**，以及各品牌的 `gameId / activityId / 商户号 / 卡号`…
> **各后端的接口与踩坑**在 `docs/` 下的五份文档：
> [易东](docs/YD_API.md) · [微租林](docs/WZL_API.md) · [企迈](docs/QMAI_API.md) · [OPPO 商城](docs/OPPO_API.md) · [奈雪点单](docs/NAIXUE_API.md)。

还有一批别的服务商的小程序也有签到（五条友、竹园村、望京小腰、大渔铁板烧、大龙燚…），
接口各不相同，本合集**按后端逐个适配**：目前支持 **吾享 / 易东 / 微租林 / 企迈 / OPPO 商城 / 品道自研** 六个
（后五个见第五节）。美团 / 有赞 / 口碑·饿了么 走**平台级账号体系**（用各自的账号登录，
签到也只是平台 H5），成本与收益不成比例，暂不做。

---

## 二、跑起来

一次配好，之后每天自动。全过程在 **[`docs/DEPLOY.md`](docs/DEPLOY.md)**，按顺序做两段：

| 段 | 做什么 | 在哪 |
|---|---|---|
| **① 搭环境** | 装 Docker → 起微信容器（**顺手把微信版本钉在已知可用的那版**）→ 验 WMPF 版本 → 挂 hook 容器 → 打两个补丁 → 建青龙 | DEPLOY.md 第 1~4 节 |
| **② 接入脚本** | 把 `wxsign/` 放进青龙脚本目录 → 配环境变量 → 加一条定时任务 | DEPLOY.md 第 4~5 节 |

> 只有**企迈系**品牌（呷哺呷哺 / 李先生牛肉面大王）多一步**一次性**动作：跑一遍
> `wxqm_auth.py` 把手机号授权过掉，之后那个品牌也是全自动的 ——
> 见 [DEPLOY.md 第 4.2 节](docs/DEPLOY.md#42-企迈系的首次手机号授权每个品牌跑一次)。
> 吾享 / 易东 / 微租林的品牌**不需要**这一步。

硬性前提：青龙、微信容器、hook **必须在同一台宿主机**上 —— token 要靠微信客户端当场产生，
服务端生成不了，所以青龙得能调宿主 docker 去操作微信容器。
微信登录也在这一段里做掉：**手机确认一次**，之后只要容器不重启就一直有效。

> ⚠️ **别跳过验 WMPF 版本那两步**（[DEPLOY.md 第 2.3~2.4 节](docs/DEPLOY.md#23-装微信时就把版本钉住建议先做)）：hook 靠 WMPF 偏移配置才挂得上，
> 而微信装成哪一版决定了能不能挂。装之前先钉版本、装之后立刻验，比事后排查省事。
> 真遇到「版本太新、上游还没适配」，`wmpf/` 下有现成工具（查上游 PR / 自己算偏移）。

配完在青龙里点「运行一次」验证 —— 日志里每个品牌打一行 `RESULT <slug> code=... msg=...` 就对了。
跑完会自动关掉小程序省内存，下次签到自己重开。

---

## 三、常用命令

```bash
python3 wxsign.py --list              # 看品牌表（含启用 / 已联调状态）+ 本次生效范围
python3 wxsign.py --find <关键词>      # 自动搜出该品牌名下的小程序并读出 appId（不用手抄）
python3 wxsign.py <slug> --probe      # 探测：直接告诉你这个号能不能签，不签到
python3 wxsign.py <slug> --probe --register
                                      # 探测 + 不是会员就注册，再复查一次 → 终判（会真实建会员）
python3 wxsign.py <slug> --discover   # 打活动原始 JSON（接入新品牌时用来找 gameId）
python3 wxsign.py <slug> --register   # 只注册会员
python3 wxsign.py <slug>              # 该品牌签到（不是会员会自动注册再签）
python3 wxsign.py <slug> --ensure     # 先自动开小程序 + 刷 token，再签到
python3 wxsign.py --all --ensure      # 所有启用的品牌
```

`--find` 干的就是你会手工做的事：打开小程序面板 → 搜关键词 → 逐张结果卡片点开 →
从逻辑层读出 appId → 再看每个号的包内特征，最后给一段可直接粘进 `brands.json` 的配置。
（包内特征只是初筛，**能不能签要跑 `--probe`**。）

每个品牌收尾都会打印一行，方便在青龙日志里 grep：

```
RESULT lakeke code=415 msg=今日已签到
```

### 签哪几个（默认 all）

不配就是 **all** —— `brands.json` 里 `enabled` 的全部。想收窄就加白名单，想永久跳过某个就加黑名单：

```bash
python3 wxsign.py --apps lakeke,laicai --ensure   # 只签辣可可、来菜（多选）
python3 wxsign.py --exclude jiucun --ensure       # 除九村烤脑花以外，其余全签
python3 wxsign.py --apps all                      # 明确不限制
```

- slug 是 `--list` 第一列那个；逗号分隔，可多选（中文逗号 / 空格 / 分号也能当分隔符）。
- **黑名单优先级最高** —— 白名单里写了、或者用了 `--all`，照样排除掉。
- 命令行盖过环境变量；直接写 slug（`wxsign.py lakeke`）等同于白名单。
- 拼错的 slug 会打警告、不静默忽略（否则「配了却什么都没跑」很难查）。
- 起跑前会把「范围 / 排除」两行打进日志，跑完看日志就能确认这次到底签了哪些。

定时任务要同样的效果：`sign.sh` 的参数**原样透传**（`bash sign.sh --apps lakeke,laicai`），
也可以做成青龙环境变量 `WXSIGN_APPS` / `WXSIGN_EXCLUDE` ——
写法见 [DEPLOY.md 第 5 节](docs/DEPLOY.md#5-配青龙)。

---

## 四、目录

```
wxsign/
├── wxsign.py            引擎：发现 appId / 探测 / 注册 / 签到 / 批量（青龙直接调它）
├── sign.sh              青龙总入口：自检 → 掉登录就点登录（要手机确认）→ 逐品牌签到
│                        参数原样透传（`--apps` / `--exclude` / 位置 slug）
├── brands.json          品牌表（品牌名 / 小程序名 / appId / gameId / 启用状态）
├── brands/              每品牌凭证（自动生成，含 token，别提交）
│   └── _template.env
├── docs/DEPLOY.md       从零部署 + 后续运维：微信侧故障、多开实例、卸干净、换微信账号
├── docs/OPPO_API.md     OPPO 商城后端逐条实测记录（会话 / 档期 / activityId 自动发现）
├── docs/NAIXUE_API.md   奈雪点单（品道自研）后端逐条实测记录（登录 / 签到 / 幂等）
├── wmpf/                hook 挂不上时的自救工具（部署阶段就要用一次）
│   ├── check_wmpf.sh       验 WMPF 版本有没有对应偏移配置（纯只读）
│   ├── hook_patch.sh       给上游打那两个必需的补丁（幂等，见 [DEPLOY.md 第 4.1 节](docs/DEPLOY.md#41-必须打的两个补丁)）
│   ├── restart_hook.sh     重启 hook（换配置 / 换微信版本 / 杀过运行时后）
│   ├── check_upstream.sh   查上游有没有人适配过你这一版（含未合并的 PR）
│   ├── auto_offsets.sh     上游没有就自己算偏移
│   ├── fetch_wechat_deb.sh 按版本下载微信包（把版本钉回已知可用的）
│   └── offsets/            算偏移的实现 + 我们自己算出的 2 个版本产出
├── wxopen.py            （容器内跑）开指定小程序；窗口/点击/安全关窗原语都在这里
├── wxfind.py            （容器内跑）按关键词搜小程序并读出 appId
├── wxident.js           （hook 容器内跑）身份采集，按 appId + mpId 挑上下文
├── wxrefresh.js         （hook 容器内跑）token 刷新，wx.login 换短效 JWT
├── wxcdp.py             （容器内跑）纯标准库 WebSocket + CDP，读小程序 appId
├── pkgprobe.py          （容器内跑）拆小程序包判供应商 / 签到能力
├── wxclean.py           （容器内跑）清残留小程序窗口（弹窗挡住关闭按钮时先遣散再关）
├── wxdom.py             （容器内跑）CDP 读小程序渲染层 DOM，按选择器+文案定位（不猜颜色）
├── wxwin.py             （容器内跑）取小程序窗口几何 —— 页面坐标 → 屏幕坐标的换算
├── wxreg.py             （容器内跑）不是会员时驱动微信授权弹窗完成注册（不需要手机号）
├── wxlogin.py           （容器内跑）点登录 → 等手机确认 → 拉全屏；过期则提示扫码
├── wxagree.py           （容器内跑）点掉「隐私保护指引」弹层（不点它页面不初始化）
├── wxnet.py             （容器内跑）拦逻辑层 wx.request，排查网络/身份用（诊断工具）
├── wxyd.py              （容器内跑）易东后端取身份：wx.login 的 code + 门店 storeid
├── wxcode.py            （容器内跑）通用取码器：只取 appId + wx.login 的 code（微租林用它）
├── wxqm.py              （容器内跑）企迈后端取登录态：storage 优先，取不到就抓真实请求头
├── wxqm_auth.py         （容器内跑）企迈「首次手机号授权」全自动 —— 每个品牌只需跑一次
├── wxoppo.py            （容器内跑）OPPO 商城取会话：读 storage 的 logininfo（NEWOPPOSID + openId）
├── wxoppo_auth.py       （容器内跑）OPPO「首次手机号快捷登录」全自动 —— 只需跑一次
├── wxoppo_act.py        （容器内跑）OPPO 自动发现当前档期的 activityId（备选：驱动 H5 + CDP hook）
├── wxnaixue.py          （容器内跑）奈雪点单取会话：读 getApp().globalData.accessToken（Bearer）
├── wxapkg.py            （容器内跑）解 wxapkg 并在里面搜 —— **不用打开小程序就能逆向**接口/签名
├── open_target.py       （容器内跑）按 appId 复核着开目标小程序（比按标题判更可靠）
├── LICENSE              保留所有权利（源码公开，但**不是开源**，见第九节）
└── README.md
```

> 标「容器内跑」的那些脚本不用你手动投递 —— 引擎每次跑都会自动
> `docker cp` 到微信容器（`ensure_helpers()`），改了代码立刻生效。

---

## 五、原理

### 为什么一套脚本能签所有吾享品牌

吾享全家共用同一套后端、同一套包裹体：

```
POST https://scrm.wuuxiang.com/crm7game-api/api/<path>
body   {"mpId":…, "openId":…, "unionId":…, "data":{…}}
header Authorization: <token>     crm7-mpId: <mpId>     [csl-GC-Shardingkey: <gcId>]
```

换品牌只换**参数**（appId / mpId / gameId），不换协议。
所以做法是**一个引擎 + 每品牌一份 `brands/<slug>.env`**：
加品牌只加配置文件，不动代码，也没有 N 份要各自维护的脚本。

### 另外五个后端：易东（eingdong）、微租林（weizulin）、企迈（qmai）、OPPO 商城（oppo）、品道自研（pindao）

上面「一个引擎签所有品牌」只对**吾享内部**成立。别的服务商接口各不相同，所以
`brands.json` 多了个 `engine` 字段来选后端 —— **不写 = 吾享**，老条目一个都不用改。
代码里对应一张分派表（`_SIGNERS`），加后端只需写一个 `do_sign_xxx()` 并注册一行。

| engine | 后端 | 认证 | 身份从哪来 |
|---|---|---|---|
| （缺省） | 吾享 `scrm.wuuxiang.com/crm7game-api` | `Authorization` + `crm7-mpId` 请求头 | mpId 从登录 token 的 payload 取 |
| `eingdong` | 易东 `zhyx.eingdong.com/api/index.php` | cookie `sessionKey=<sessionId>` | `wx.login` 的 code + ext 里的 storeid |
| `weizulin` | 微租林 `saas.funjs.top/api` | `Authorization: Bearer <JWT>` | `wx.login` 的 code（换 token 时用） |
| `qmai` | 企迈 `webapi.qmai.cn/web/cmk-center` | `Qm-User-Token` + `store-id` 请求头 | `wxqm.py` **双路**：storage 的 `loginData`，取不到就抓真实请求头 |
| `oppo` | OPPO 商城 `msec.opposhop.cn`（H5 侧 `hd.opposhop.cn/api`） | `NEWOPPOSID` + `openid` 请求头 | `wxoppo.py` 读 storage 的 `logininfo`（`encryptedSession` / `openId`） |
| `pindao` | 奈雪点单 `tm-api.pin-dao.cn`（品道自研） | `Authorization: Bearer <accessToken>` | `wxnaixue.py` 读 `getApp().globalData.accessToken`（JWT，120 天） |

非吾享后端多一个**首次接入**的动作（每个品牌一辈子一次）：

| engine | 首次要做什么 | 谁做 |
|---|---|---|
| `eingdong` / `weizulin` / `pindao` | **什么都不用** —— 服务端自己持 appsecret 换 openid，无授权弹窗 | — |
| `qmai` | 过一遍**手机号授权**（企迈要求已绑手机号的会员） | `wxqm_auth.py`，全自动 |
| `oppo` | 过一遍**手机号快捷登录**（OPPO 要求绑手机号） | `wxoppo_auth.py`，全自动 |

**易东**（`刘一手`）：**没有签名、没有 nonce**，就是一个 cookie —— 比吾享还简单。

```
wx.login() 取 code        ─┐ 在小程序逻辑层取（wxyd.py）
wx.getExtConfigSync().storeid ─┘
        ↓
POST /api/login          {code, storeid, …}  →  {sessionId, openId, store_name}
        ↓
POST /signin/get_info    cookie: sessionKey=<sessionId>   →  signed_today
POST /signin/check_in_1  cookie: sessionKey=<sessionId>   →  签到
```

**微租林**（`许家去水印`）：base 与平台应用 ID **都明文写在包内的配置对象里**，
认证是一个 JWT（有效期 7 天）。

```
wx.login() 取 code                       ─┐ 通用取码器（wxcode.py）
        ↓                                 │
POST /open/auth/mp/silent-login  {code}  → data.token（JWT）
        ↓
GET  /open/check-in/status   Authorization: Bearer <token>  →  todayChecked
POST /open/check-in          Authorization: Bearer <token>  →  签到
```

两家能做成的共同原因：**code 都由服务端自己持 appsecret 去换 openId** —— 我们不需要 secret
（传假 code 会返回微信的 `errcode:40029` / `invalid code`，正好证明这点），且全程**无人工确认**。

**企迈**（`呷哺呷哺`）：认证只要两个请求头、**没有签名**，比吾享还简单。
身份走 `wxqm.py` 的**双路**（有的商户持久化、有的不持久化）：

```
① storage 有 loginData  → 直接读 token + store.id（实测：李先生会写）
② storage 是空的        → 开 CDP Network domain，从它自己的请求头里读
                          Qm-User-Token + store-id（实测：呷哺走的是这条）
        ↓
POST /web/cmk-center/sign/userSignStatistics   {activityId, storeId}  →  今天签没签
POST /web/cmk-center/sign/takePartInSign       {activityId, storeId}  →  签到
```

> ⚠️ 四个坑 —— 每一个都让我错判过一轮，细节见 [`docs/QMAI_API.md`](docs/QMAI_API.md)：
> 1. **真正的是 `cmk-center/sign/*`**。包里另有一套 `integral/sign/*`（积分商城那套），
>    它对同一商户会回 **`400042 商家未开启此功能`** —— **那是误导性错误码**，
>    我据此连着下了三次「商户没开活动」的错误结论；
> 2. **`cmk-center` 不带业务线前缀**：写成 `/web/mealmate-apiserver/cmk-center/...` 会回
>    `43004 http状态码异常`。业务线只体现在 `Qm-From-Type` 头里；
> 3. **路径必须带 `/web`**：少这一节，阿里云 WAF 会对「不存在的路由」甩回 110310 字节的
>    JS 挑战页（`Punish-Loc: keepper`）—— 极易被误读成「被墙了」，其实只是 URL 拼错；
> 4. **必须已绑手机号**：未登录时签到页**根本不发请求**（`onClickCheckin` 先弹授权），
>    接口层回 `100005 用户未登录`。绑定是**一次性**的（服务端绑 openid，永久），
>    绑完就与日常签到无关了。首次那一步**也已经做成脚本** —— `wxqm_auth.py`
>    （CDP 调 `popAuthorization()` → 点 `getPhoneNumber` 按钮 → 系统级输入点原生「允许」框）。
>
> `activityId` 是**商户级活动的固定 ID**（随签到入口由服务端下发，包里没有），
> 所以配在 `brands.json` 的 `qm_activity` 里。

**OPPO 商城**（`OPPO商城`）：**唯一一个不是"会员积分"的后端** —— 是 OPPO 官方商城的
「每日签到得积分」。会话是两个**请求头**（无签名）：

```
wxoppo.py 读 storage 的 logininfo  →  NEWOPPOSID（= encryptedSession）+ openId
        ↓
GET  /cn/oapi/marketing/cumulativeSignIn/getSignInDetail?activityId=…
        →  todaySignIn（服务端背书的「今天签过了」）+ baseAwards（7 天，status=1 已签）
POST /cn/oapi/marketing/cumulativeSignIn/signIn  {activityId}   →  签到
```

> ⚠️ **三个坑**（每个都让我错判过一轮，细节见 [`docs/OPPO_API.md`](docs/OPPO_API.md)）：
> 1. **别用「键名含 token 就当 sid」的启发式** —— storage 里还有个 `constToken`，
>    会被误取走 → 服务端回 `403 用户未登录`。认死 `logininfo.encryptedSession`。
> 2. **`activityId` 会随档期换**（实测 7 月 → 9 月），而**旧档期的 `getSignInDetail`
>    照样回 200**（返回那 7 天的陈旧数据）—— 只有 `signIn` 才会回 `5007 活动已经结束`。
>    极易误判成「活动还在、只是签不上」。所以**幂等判据用 `todaySignIn`，
>    不要靠"试签一次看错误码"**（那还会把 `5007` 和「今天签过了」混在一起）。
> 3. **`activityId` 现在是全自动发现的**（不用每月手动拿一次）：承载签到页的 H5 是 **SSR**，
>    HTML 里内联了活动 DSL，其中 `SignIn_*` 组件的配置就写着 activityId →
>    一次普通 GET 拿到（实测 0.3s）。失败时退到备选：驱动 H5 + CDP hook 抓它实际发的请求
>    （`wxoppo_act.py`）。遇到 `5005/5007` 还会**自动重新发现 + 用新 ID 重试一次**。
>
> 与企迈的**不同点**值得注意：企迈的 `activityId` 是商户级固定 ID（配在 `qm_activity`），
> OPPO 的是**按档期滚动**的 —— 所以 OPPO 必须能自己发现，不能只靠配置。

**奈雪点单**（`奈雪点单`）：**品道自研**（`pin-dao.cn`），「奈雪币」每日签到。
⚠️ 别被包里的 `qmai.cn/naixue-login` 骗了 —— 企迈只承担登录/会员的一部分，
**签到不在企迈、也不在 H5**，是小程序原生页自己调的接口：

```
wxnaixue.py 读 getApp().globalData.accessToken  →  Bearer（JWT，120 天）
        ↓
POST /user/sign/save   {signDate:"2026-9-27"}
   ← 页面 pkgBasics/pages/signInReminder/signInReminder 一 onShow 就自动调它
```

> ⚠️ **三个坑**（细节见 [`docs/NAIXUE_API.md`](docs/NAIXUE_API.md)）：
> 1. body 是**两层** `{common:{…}, params:{…}}`，业务入参一律进 `params`；且网关
>    **强校验 `common.nonce`** —— 漏了它直接回 `{"error_msg":"invalid request body, no nonce"}`。
> 2. `signDate` **不补零**：原码是 `getFullYear()+"-"+(getMonth()+1)+"-"+getDate()`
>    → `2026-9-27`，**不是** `2026-09-27`。
> 3. `common.openId` 是原码里的**硬编码常量**（`QL6ZOftGzbziPlZwfiXM`，不是真 openId），
>    签名 = `Base64(HmacSHA1("nonce=…&openId=…&timestamp=…", salt))`，照抄即可。
>
> 该接口**幂等**（同一天重复调都回 `code=0`，不会重复发币），所以**不需要"先查后签"**；
> 签到成果用 `/user/memberCenter/userAsset` 的 `coin`（奈雪币余额）佐证。

逐条实测记录见 [`docs/YD_API.md`](docs/YD_API.md)、[`docs/WZL_API.md`](docs/WZL_API.md)、
[`docs/QMAI_API.md`](docs/QMAI_API.md)、[`docs/OPPO_API.md`](docs/OPPO_API.md) 与
[`docs/NAIXUE_API.md`](docs/NAIXUE_API.md)。

### 签到走同一套 sign 接口

**吾享全家的签到是同一个接口**（`/api/game/sign/*`），与辣可可完全一致。

| 接口 | 作用 | 实测返回 |
|---|---|---|
| `/api/game/sign/detail` | 活动详情（规则文案 / 档期） | `200` |
| `/api/member/sign/survey` | 我的签到统计 | `200` `{signNum, cumulativeSignNum, lastSignDate}` |
| `/api/game/sign/monthDetail` | 本月日历（每天状态） | `200` |
| **`/api/game/sign/signIn`** | **签到** | `200` 成功 / **`415` 今日已签到** |

所以代码不依赖静态分类：直接打 `sign/detail`，认（200）就走这套接口。
`lot/*` 那套是**抽奖**（大转盘），不是签到，别拿它当签到调。

> **判断「这个号能不能签」不能只看一个接口**（判据修了四轮，每轮都是踩出来的）：
>
> | 看什么 | 为什么不够 |
> |---|---|
> | 包里有 `pages/sign` 字面量 | 只证明**壳**有能力，证不了这个租户开了活动 |
> | `lot/list` 返 `200` | 辣可可这类 sign 型号在这里返 `405`，会漏掉它 |
> | `sign/detail` 返 `200` | **只说明这个 gameId 有效** —— 抽奖/秒杀的 gameId 也返 200 |
> | `sign/survey` 返 `200` | **只对「已经是该品牌会员」的号成立**，非会员答不了 |
>
> 可靠的定性判据是 **`sign/detail` 里的 `isCumulativeSign` 非 null**：它不依赖会员资格，
> 而且能把两类活动分开 —— 过桥缘签到送积分 = `0`（是签到），蜀大侠周三抽奖 / 农耕记秒杀 = `null`（不是签到）。
> 所以 `--probe` 先用它定性，能用 `sign/survey` 定量时才用 survey 给「确认能签」。
>
> 上表最后一行是踩过的坑：过桥缘游戏中心的签到活动**名字就叫「过桥缘签到送积分」**、
> `sign/detail` 返 200，但因为当时还不是会员，`sign/survey` 返 `411`，被误判成「不是签到」。
> 真跑一遍自动注册后立刻签到成功。所以 `--probe --register` 才是终判：
> 需要时给这个号注册会员，再复查一次 survey。

### 认证链路（这也是为什么必须有微信）

```
① 自动把目标小程序打开（面板搜索 → 点结果卡片，开错号会自动关掉重试）
② CDP 读到小程序逻辑层 → 调 wx.login() 拿 jsCode
③ POST wechat.wuuxiang.com/i5xforyou/auth/login {code, mpid} → 换到 token（短效 JWT，约 110 分钟）
   同时把 mpId / openId / unionId / 会员卡号 一起读出来写进 brands/<slug>.env
④ 带 token 打 scrm.wuuxiang.com/crm7game-api/api/* 做签到
```

token 每次现取现用，没有「抓一次长期用」这回事。
换不到 token 时的标准动作是**关掉小程序重开**（旧窗口里的登录会话会失效，
`wx.login` 的 code 拿去换会返 `invalid code`）—— 脚本会自己重试一次。

> **`mpId` 从哪来**：它不是小程序包里的常量（全盘 grep 490 个 `.wxapkg` 零命中），
> 而是**服务端下发**的。小程序自己登录后会把登录 token 存进 storage
> （键名形如 `authData-<随机串>`），**这个 JWT 的 payload 里就带着 `mpid`**：
>
> ```json
> {"sub":"<openId>","appid":"<小程序 appId>","iss":"mobile","exp":…,"mpid":"gh_xxxxxxxxxxxx"}
> ```
>
> 所以身份一律从「开号后读 storage → 解 token payload」取，**与小程序模板无关**。
> 早期版本只按 `storage` 里的键名叫 `mpId` 去取 —— 那只在一部分模板上成立
> （实测 `pages/home/index` 有、`pages/cardhome/home/index2` 没有），
> 表现就是一批号全报「拿不到 mpId」。

### 界面操作怎么找按钮：按**结构 + 文案**，不按颜色

早期版本靠像素判据（颜色 + 宽高比 + 像素密度）找按钮 —— 实测**必然失效**：
不同品牌的按钮可能是橙的、红的、图片、纯文字；同一品牌不同页面也不一样。
连着调了三轮阈值仍在补漏洞（把活动卡里的绿色农田图当按钮、把橙色文字行当按钮……）。
⚠️ 最典型的一次：企迈有**几十上百个商户**，主题各自跟活动走（呷哺同一天里授权按钮
先是橙、后来变蓝；李先生是蓝）—— **颜色判据从根上不成立，每接一个新商户都要改阈值**。

现在改成**问页面自己**：CDP 连到小程序的**渲染层**（context 里有 `wx-view`/`wx-button`
自定义标签的那个），读 `getBoundingClientRect()`，于是「找按钮」= 「找匹配的元素」：

```
① 词根命中（双字以上）：签到 / 打卡 / 参与 / 领取 / 抽奖 / 允许 / 同意 / 确认 / 授权 …
   命中后取面积最小的那个（最小 ≈ 真实按钮，而不是套着它的容器）
② 排除词过滤：含 记录 / 规则 / 商城 / 上个月 / 查看 … 的一律不点
③ 都命中不了 → 默认什么都不点（`WXSIGN_REG_FALLBACK=1` 才启用启发式兜底）
   —— 实测兜底会选到「1积分」「已连续签到1天」这类非按钮元素，误点比不点更糟
```

#### ⚠️ 一个页面对应**多个渲染面** —— 先说要找什么，再看哪个 ctx 有

这是本项目踩过**最深**的一个坑，值得单独讲清楚。

`wxdom.py` 原本挑 ctx 的判据是「**元素最多的那个**」。企迈签到页弹授权层时实测：

| ctx | 节点 | wx-* 标签 | 有授权层？ | 是什么 |
|---|---|---|---|---|
| 6 | 302 | 161 | ❌ | 底层页面（「我的」页） |
| **9** | 221 | 152 | **✅ `#authorization`** | **授权层在这儿** |
| 10 | 11 | 0 | ❌ | 空壳 |

三者 url 相同、title 都是 `Page-Frame`。于是「谁元素多选谁」**必然挑到 ctx=6** →
在里面找授权层当然找不到。

**我当时的错误结论**：这个坑把我带偏了整整一轮 —— 我先断定
「授权层不在渲染层 DOM 里」，并据此认为只能走像素、写了个按**橙色**找按钮的探测脚本。
**两处都错**：前提错了（它就在渲染层），方案也错了（按颜色找品牌按钮必然失效）。
真相是：**不是「找不到」，是「找错了地方」。**

正解是把顺序反过来 —— **先说要找什么，再看哪个 ctx 有**：

```python
wxdom.find_ctx_with(["#authorization"])        # → {9: 1}
wxdom.rect_of(ws, 9, ("手机号一键登录",))       # → WX-BUTTON (205,589) 284x48
wxdom.clickable(ws, 9, ".i-circle")            # → 勾选框 (35,740) 19x52
```

> ⚠️ 那个勾选框的**两态是两个不同的类名**，不是一个类的两种样式 —— 实测：
> **未勾选 = `.i-circle`**（空心圆），**勾选后 = `.i-xuanze_xuanzhong`**（选择_选中，实心勾）。
> 所以只写 `.i-circle` 有个**隐蔽的坑**：一旦它**本来就勾着**（上一次弹层留下的状态），
> `.i-circle` 命中 0 个 → 脚本误以为「选择器都不行」→ 掉进「按说明文字反推」的兜底，
> 反推出 `(6,590)` 这种坐标**乱点**（实测踩到）。
> 正解是**两态都认**：先查「是不是已勾（`.i-xuanze_xuanzhong`）」→ 已勾就跳过，
> 没勾才去点 `.i-circle`（`wxqm_auth.CHECKED_SELECTORS` / `CHECK_SELECTORS`）。

顺带一个认知：**授权层是小程序自己的普通 view 组件**，不是原生浮层 ——
源码就一句 `onAuthorization(){ this.selectComponent("#authorization").show() }`
（`pluginMarketing/components/authorization-4e03c673`），渲染成 `<wx-std-authorization>`。
所以只要找对 ctx，它和页面里别的元素没有任何区别。

> ⚠️ 还想用**逻辑层**绕开这个问题的，别再试了 —— 实测四条路全封死：
> 页面 data 的 46 个字段里没有一个跟授权层相关；`wx.createSelectorQuery()`
> 对 14 个选择器全部返回空；`Runtime.executionContextCreated` 事件这个代理**根本不发**
> （所以找逻辑层只能**盲撒 ctx 1..80**，凭 `wx.getAccountInfoSync()` 能拿到 appId 认出它是 ctx=3）。
>
> ⚠️ 组件 hide 之后**节点会留在 DOM 里**（只是矩形塌成 0），
> 所以「`#authorization` 在不在」**不能**当「授权层有没有弹出」的判据 ——
> 要用 `find_ctx_with(..., visible=True)`。
>
> ⚠️⚠️ 而 `visible=True` **不能只看命中节点自己的矩形**（这条同样反直觉，实测栽过）：
> 授权层是「**容器根塌陷、内容在子节点**」的典型 ——
>
> ```
> WX-STD-AUTHORIZATION#authorization   自身 w=0 h=0 x=0 y=1188   ← 视口才 410x779
> 但子树里有 27 个可见节点，其中一个 WX-VIEW 410x779（铺满整屏）
> ```
>
> 只判根节点 → 会把**明明弹着的授权层判成「没弹出」**，然后整条链路默默走错分支。
> 所以判据是「**命中节点自身或其任一子孙可见**」。
>
> ⚠️ 还有个纯粹的**性能坑**：`find_ctx_with` 一次调用 = 盲撒 80 个 ctx + 等响应，
> 早先写成 `for sel in AUTH_SELECTORS: find_ctx_with([sel])` 逐个查 —— 4 个选择器
> 就是 4 轮 ≈ 48 秒，真机上表现为「脚本像卡死了」。**选择器要一次全传进去**
> （同一个 ctx 内它们是**或**的关系，合起来查语义一样、只花一次的钱）。
> 另：`_broadcast` 的收集窗口现在会把 socket 超时压到 2.5s，响应到齐就断，
> 不再干等满 —— 正常从 12s 降到 ~2.5s。

坐标是**窗口坐标**，不是屏幕坐标。小程序窗口是独立窗口、位置不固定
（实测 410x776 @ 435,124），所以：

```
屏幕坐标 = 窗口原点 + 页面坐标        # DPR=1，已实测
```

为什么不用补偿标题栏高度：用 CDP 问页面自己得到
`window.screenX/screenY = (435,124)`（与窗口原点完全一致），
且 `innerHeight(779) > outerHeight(776)` —— 顶部那条「⌂ 首页 ●●● ─ ⊙」是**覆盖层**，
不占视口。取窗口几何由 `wxwin.py` 负责。

> 窗口为什么不是铺满屏幕的 1280x1024：那需要改云微 openbox 的一条通配最大化规则，
> 见 [DEPLOY.md 第 2.5 节](docs/DEPLOY.md#25-让小程序以手机竖版运行建议做)。不改也能跑（页面坐标恰好等于屏幕坐标），
> 但那是巧合 —— 窗口一挪位置就全错。

### 未来形态：侧边栏 / 分栏也兼容

小程序未必永远是「独立窗口」—— Windows 版微信就有把它嵌在主窗口里、不居中的形态。
那种情况下**按窗口标题去找「独立小程序窗口」会整个失效**。

所以坐标系是**优先问页面自己**（CDP 的 `window.screenX / screenY / innerWidth / innerHeight`），
拿不到才回退到窗口探测。页面自己报的位置和尺寸**两种形态都成立**
（实测差别可见：CDP 报视口高 779，窗口探测报 776 —— CDP 更准，因为不含边框）。
而且每轮都刷新，窗口被拖动、布局变化都能跟上。

> ⚠️ 边界说清楚：这只解决了**坐标系**这一层。真到侧边栏形态时，
> **打开小程序的流程**（面板搜索 → 点结果卡片）和**关闭窗口**仍按「独立窗口」写的，
> 那部分需要另做。

**点偏了能回来吗 —— 能，两个机制：**

1. **只点认识的元素**：命中词根才点，命中不了默认什么都不点，所以「误点」本来就少。
2. **跑偏恢复**：如果入口点完、又连滚 3 轮，这一页**始终没有任何可点目标**，
   就判定「大概点偏到别的页面了」→ 点**标题栏左上角**退回去重来（上限 2 次，
   避免来回绕圈）。那个按钮：页面栈 >1 层时是「`<` 返回」，只剩 1 层时是「`⌂` 回首页」，
   位置相同，所以不管跑多远都点得回来。
   **坐标是探测出来的**，不是写死的比例：在标题栏带里找**最靠左的紧凑字形块**
   （`wxwin.glyphs()`，算法同 `wxopen.probe_close_glyph`）——
   换图标、换字号、换主题色都不影响；探测不到才退回经验比例。
   实测探测结果 (18,43)，与手工点中的 (23,40) 落在同一个图标内。

实测日志：

```
[ui] DOM 文案「打卡签到」→ 点 (192,249)
[ui] 第2步：无按钮 → 滚动找目标（第 1/3 次）
...
[nav] 入口点完了、也滚了 3 轮，这页始终没有可点目标 → 疑似点偏到别的页面，退回上一层重来（第 1 次）
[nav] 点标题栏左上角「返回 / 回首页」(23, 40)（页面坐标）
[ui] DOM 文案「打卡签到」→ 点 (192,249)        ← 回到首页，又认出来了
```

这条与主题、颜色、尺寸、品牌都无关。**文案不一样也能配**：
`WXSIGN_REG_KEYWORDS=签到,打卡,领福利`、`WXSIGN_REG_EXCLUDE=记录,规则`。

两个边界：

- **微信原生弹窗不在小程序 DOM 里**（手机号授权的「允许」）→ 那部分只能走**像素**
  （好在它的文案/颜色都是**微信客户端**定的，不随品牌变，所以颜色判据在这里反而可靠）。
- **别把 UI 当主路径**：签到本来就该走 API（接口参数与文案无关）。
  UI 只用于**注册**这一跳 —— 因为注册要微信授权、抓不到接口。

### 不是会员怎么办

签到接口要 `memberId / cardId / cardNo`，只有会员才有。不是会员时脚本会自动注册，两条路：

**A. 自动注册（默认，不需要手机号）**
驱动界面走四步，手机号由微信从你自己账号里给 —— **全自动**，你不用提供任何东西：

```
① 点登录入口       按 DOM 文案找（「立即登录」/「请登录」各品牌不同，用词根 + 短文案优先）
② 点「允许」       微信原生手机号授权弹窗（手机号已授权过时会跳过这一步）
③ 弹出注册表单     小程序自己的（wx-user-info 组件，含手机号/昵称/卡密码/邀请码）
④ 点表单提交       「确认授权开通并绑定会员」→ member/single 立刻 401→200
```

由仓库内置的 `wxreg.py` 负责。

**B. API 直连（可选，更省事）**
**配了手机号才走这条**：零 UI，直接调

```
POST /api/member/register   data={mobile, gameId, thirdShopId, byInviteCode}
```

`mobile` 是明文手机号。来源：`brands/<slug>.env` 的 `WX_REGISTER_PHONE`，
或环境变量 `WXSIGN_REGISTER_PHONE`（配一次所有品牌共用）。

> 两条路都不需要手工操作。唯一需要配置的例外：账号绑了**多个**手机号时，走 UI 要指明选第几个
> （`WX_REGISTER_PHONE_INDEX`，从 1 开始；只有 1 个号时不用设）。
> 同一个人可以在多个品牌各注册一次会员，互不影响。

### 登录态会过期吗？什么时候真的需要你

三层东西，时效完全不同 —— **只有第一层要人**：

| 层次 | 存在哪 | 时效 | 失效后 |
|---|---|---|---|
| **微信客户端登录** | 微信容器里 | 长期 | ⚠️ 要你在手机确认一次（只有重启机器/容器、或微信自己掉登录才会） |
| 小程序登录态（本地 token） | 小程序自己的存储 | ~110 分钟（JWT） | 脚本自己续：重开小程序 → `wx.login` 拿新 code → 换新 token |
| **品牌会员身份** | **服务商的服务器**上，绑 openId | **永久** | 不用管 —— openId 不变，会员关系就一直在 |

实测（2026-09-25，`survey/fromzero_test.log`）：把 `brands/<slug>.env` 里的身份字段
**全部清空**、小程序窗口也关掉，再跑一次日常签到 ——

```
[ensure] 我的小板凳街坊火锅 → rc=0 (已就绪)     ← 自动开小程序
[token]  刷新 rc=0                             ← 自动重新抓身份 + 换 token
[member] 会员 OK：积分=0.0 卡号=6002215523      ← 会员自动认出，卡号与之前完全一致
RESULT audit21 code=415 msg=今日已签到
[ui] 出现次数 = 0                               ← 全程零 UI 点击
```

所以：

- **会员已存在时，日常签到是纯 API、零 UI** —— 登录态过期也不影响，脚本会自己续。
- **注册只需要一次**（首次接入某个品牌）。注册完会员关系就记在服务商服务器上了，跟你本地文件无关。
- **唯二要人出手的情况**：① 微信掉登录（重启机器/容器之后）；② 想换微信账号（走 [`--reset-identity`](#三常用命令)）。

---

## 六、接入一个新品牌

```bash
# 1) 一条命令把 appId 找出来（它自己开面板搜、点开卡片、读 appId、判包）
python3 wxsign.py --find <品牌关键词>
#    把它打印的 JSON 粘进 brands.json 的 brands 数组（slug 自己改成英文）

# 2) 跑一次 —— 它会自己开小程序、抓身份（mpId/openId/memberId/卡号）、刷 token
python3 wxsign.py <slug> --ensure

# 3) 探测：--probe 直接给结论（✅ 能签到 / 🔵 是签到活动但还没注册 /
#    🟡 有活动但不是签到 / ⚪ 服务端没配活动）；加了 --register 就是终判
python3 wxsign.py <slug> --probe
python3 wxsign.py <slug> --probe --register
python3 wxsign.py <slug> --discover      # 打活动原始 JSON，用来填 gameId

# 4) 联调
python3 wxsign.py <slug>
```

**除了给 `brands.json` 加一条（appId 由 `--find` 自动读出、gameId 见第 3 步），其余全自动。**
（上面 4 个品牌的 appId 和辣可可的 gameId 都已经在 `brands.json` 里了，所以现在也是零手工。）

> 加**非吾享后端**的品牌时多填一个 `engine`（例如 `"engine": "eingdong"`），见第五节。
> 缺省不填 = 吾享 —— 只加吾享品牌的话可以无视这个字段。

### 零 UI 侦测：**先解包**，再决定要不要打开

**在打开小程序之前**就能把它看透 —— 包本来就缓存在本机：

```bash
python3 wxapkg.py feat <appId>                    # 判厂商 + 找签到/抽奖/积分商城（关键词命中）
python3 wxapkg.py grep <appId> '签到' --ctx 80     # 任意正则 + 上下文
python3 wxapkg.py files <appId>                   # 列页面/模块路径
```

**原理**：PC 微信的 wxapkg 带 `V1MMWX` 头，但**只有首 `6+1024` 字节**是密文，
其余正文整体只做了单字节 XOR，密钥 `ord(appId[-2])` —— 纯标准库就能还原明文。
> ⚠️ 别按标准 wxapkg 索引段去解析 —— 那条路在 V1MMWX 上必然越界报 `struct.error`，
> 曾据此误判成「解包方法失效」，其实只是没走 XOR 这条路。

**实测价值**（省下的时间很可观）：
- 判某品牌**到底有没有签到**、走哪个接口、送什么 —— 不用开小程序；
- 逆向 API 的 **base 域名 / 路径前缀 / 认证头 / 签名算法**（OPPO 与蜜雪都是这么挖出来的）；
- 看**分包名**（`_pages_coupon_` 这种）就大致知道功能结构。

但包是**静态**的 —— 想知道运行时的状态（哪个账号、积分多少、接口到底回什么），要靠下面的逻辑层。

### 小程序有**两个** JS 上下文，`wx` 只在逻辑层

同一个 CDP 连接里能摸到两类 ctx（实测蜜雪冰城）：

| ctx | 是什么 | 里面有什么 |
|---|---|---|
| `3` | **逻辑层**（Service） | `wx`（**747** 个 API）、`getApp()`、`wx.request`、`wx.getStorageSync`、`getCurrentPages()` |
| `6`/`8` | **渲染层**（WebView） | `window`/`document`；`wx` 只有 289 个只读桩，**`wx.request` 是 `undefined`** |

于是分工很明确：
- **读 storage / token / 页面 data、调页面实例上的方法、hook `wx.request`（连响应一起抓）→ 逻辑层**；
- **按文案点按钮、看 DOM → 渲染层**（`wxdom` 选的就是它，因为按"节点最多"挑）。

> 这也修正了早期文档里「渲染层没有 `wx` 对象」的表述 —— 准确说是
> **`wxdom` 挑中的那个 ctx** 是渲染层，而逻辑层 ctx 一直都在、只是没被挑中。
> 逻辑层里能直接读 `getApp().accessToken`，也能调页面实例上的方法
> （实测：`getCurrentPages()` 里 `pages/mine/index` 上挂着 `signTask`，
> 直接调它 ≡ 用户点了签到；hook `wx.request` 还能把小程序**自己**发的请求连同响应一起抓到）。

---

## 七、已知限制

1. **登录是一次性配置，但要留意「别让它掉」。** 首次扫码一次即可，之后只要容器不重启
   就一直有效、运行期不需要人工。⚠️ 一旦因重启掉登录，恢复时**必须在手机上确认**
   （Linux 版没有 Windows 那个「登录免确认」选项），拖久了还会转成扫码；这两种情况
   `sign.sh` 都以退出码 `2` / `3` 中止，不会假装成功。运维要点就一条：**容器与宿主都别重启**。
   详见 [DEPLOY.md 第 6 节](docs/DEPLOY.md#6-微信侧常见故障这部分是运维的大头)。
2. **青龙与微信必须同机**（[上面那节](#二跑起来)的前提）—— token 靠微信客户端产生，服务端自举不了。
   除此之外**零手工**：身份、token、会员信息、活动 id 都由脚本自己取。
3. **同一时刻只能开一个小程序** → 多个品牌是**串行**的，每个约 1~2 分钟。要提速就用
   [上面那节](#签哪几个默认-all)把范围收窄。
   ⚠️ 这条不只是「慢」：`wxdom` 挑的是**节点最多的那个 ctx**，也就是**渲染层** ——
   那里 `wx` 只是只读桩（`wx.request` 是 `undefined`）、**拿不到 appId**。
   两个小程序同开时它会**选到别人的页面**（实测：目标呷哺、实际选到李先生）。
   → 跑之前**先确保只开目标小程序**（`wxclean.py` 负责清残留）。
   （**逻辑层 ctx 一直都在**、`wx` 是完整的 —— 见第六节「两个 JS 上下文」；
     日常签到走的就是逻辑层，一直是按 appId 挑上下文的。）
4. **注册走微信授权弹窗，不需要手机号**（已真机跑通）。配 `WXSIGN_REGISTER_PHONE`
   可改走 API 直连，但那条路**没真发过请求** —— 发出去就在账号上真实建会员。
5. **企迈 / OPPO 商城的「首次手机号授权」要单独跑一次，引擎不会自动替你跑。**
   每个品牌一辈子一次，跑法见[第一节](#一能签到的小程序12-个)。没跑过的症状很好认：
   `RESULT xxx code=NOIDENT`。
   - **企迈**：`NOACTID` = `brands.json` 里缺 `qm_activity`（活动 ID 见该字段注释）、
     `NOIDENT` = 拿不到企迈登录态（小程序没开 / hook 不通）；
   - **OPPO 商城**：`NOIDENT` = 拿不到 OPPO 会话（`wxoppo.py`）、
     `NOACTID` = activityId 自动发现与配置都为空。
     ⚠️ OPPO 的 `activityId` **按档期滚动**（7 月 → 9 月换过一次），
     但**引擎会自动发现**它 —— 换档期、甚至换档期时签到失败，都会自动重新发现并重试，
     通常不用人工介入。只有两条发现路径都失效（OPPO 改版）时才需要按
     [`docs/OPPO_API.md`](docs/OPPO_API.md) 第五节手工确认。
6. **界面自动化会碰到「覆盖层」，但绝大多数都能按结构找、不必猜颜色**：
   - **小程序自己的授权层 / 弹层**（企迈的「欢迎加入<品牌>」那一层）→ 走 `wxdom`
     **按选择器 + 文案**定位，一次颜色判断都没有
     （[那节](#界面操作怎么找按钮按结构--文案不按颜色)有完整判据）。
     ⚠️ 品牌小程序的按钮**绝不能**按颜色找 —— 企迈有几十上百个商户，主题各自跟活动走
     （实测呷哺的授权按钮先是橙、后来变蓝；李先生是蓝）。**颜色判据从根上不成立。**
   - **H5 / web-view 自带的弹层**（OPPO 商城的「订阅提示 / 我知道了 / 去设置」）→
     同样**按文案点**，但 ⚠️ **别用 Escape**：实测 Escape 对它画面变化 **0.0%**
     （Escape 只对**微信原生**框有效），而点「我知道了」能关（画面变化 **24.8%**）。
     这个框**一打开小程序就弹**、盖在最上层 → 之后点底部导航/按钮**全部落空**，
     现场像"坐标算错了"。清法是 `wxdom.dismiss_dialogs()`：
     按文案找 → 点 → **画面差异验证**（有框继续清、没框一次都不多点）。
   - **微信原生弹窗**（手机号授权的「允许」、退出登录确认框）→ 这个**才**只能用像素。
     判据是**颜色**，理由是它的文案与配色由**微信客户端**定死、不随品牌/活动变。
     这是唯一允许用颜色的地方。
   - ⚠️ **授权层不在「小程序的 DOM 之外」，别被表象骗了**（这里踩过很深的坑）：
     它一直在渲染层里，只是渲染在**另一个渲染面**上。实测呷哺签到页弹授权层时，
     同一时刻有两个 `Page-Frame` 渲染面：
       · ctx=6：302 节点 / 161 个 wx-* 标签 / **没有授权层** ← 底层页面
       · ctx=9：221 节点 / 152 个 wx-* 标签 / **有授权层** ← `#authorization` 在这儿
     两者 url、title 全一样。而 `wxdom` 默认挑「元素最多的 ctx」→ **必然挑到 ctx=6**。
     我一开始因此断定「授权层不在渲染层」，还据此写了按颜色找按钮的探测脚本，
     **结论和方案双错**。真相是**不是「找不到」，是「找错了地方」**。
     正解：`wxdom.find_ctx_with(["#authorization"])` —— **先说要找什么，再看哪个 ctx 有**。
     （另：组件 hide 之后**节点会留在 DOM 里**，只按选择器判断会误以为它还开着；
     判断「有没有弹出」要用 `visible=True` 那个变体。）
7. **扫码登录的过期检测是启发式的**：脚本只能判断「屏幕上有二维码」，
   不能断定「已过期」（等确认页也可能带码）。所以它一律报「疑似」并留截图，
   不替你下结论。
8. **风控**：在非官方环境运行微信本身违反其条款，建议用闲置小号，先跑一两周再定型。
   服务商侧也留了口子 —— 辣可可的签到规则里明确写「非正当手段获得的积分会被清零」。
9. **界面自动化有三类「环境病」，引擎会自愈，但知道一下更好**：
   - **模态框吞点击**：微信弹「退出登录？」这类确认框时，之后所有点击都落不到实处，
     症状伪装成「小程序面板打不开 / 侧边栏点不动 / 坐标算错了」。引擎会在动手前、以及逐行点结果
     之前各查一次（结构判据：`xwininfo` 里**无名、无 WM_CLASS、尺寸像对话框、而且屏幕居中**的顶层窗口
     —— 「居中」这条不能省，否则主窗口搜索框那个同样无名的**下拉浮层**会被误判成框，
     反而把点结果的坐标带歪），并且**先激活再点** —— 这种原生框不吃未激活状态下的合成点击。
     只点**取消**那一侧，绝不碰绿色按钮。
   - **关不掉的推广窗口**：小程序面板里的推广位（标题像「华夏家博」「永伟美发店」）点关闭按钮无反应，
     攒到五六个就把侧边栏堵死。打不开面板时引擎会硬清运行时并自动重启 hook 再重试一次。
   - **面板整个开不出来**：实测过这种实例 —— WMPF 进程健康、没有僵尸、点击也正常，
     但点侧边栏「小程序」图标窗口树毫无变化。这时引擎**不再纠缠面板**，改用**主窗口顶部搜索**
     直接搜小程序并点开（不依赖面板，开完用 appId 复核），这条兜底从 2026-09-25 起默认启用。
   三者都只在**批量跑**时才明显 —— 这也是 `--all` 要串行、每个品牌约 1~2 分钟的原因。

---

## 八、来历

本仓库最早是给辣可可写单个签到脚本时攒下的一套微信侧自动化，后来发现吾享系品牌共用同一套后端，
就把它泛化成了这个合集。所以 `wxident.js` / `wxrefresh.js` / `wxopen.py` 这些文件
在早期的单品牌项目里都有对应的前身 —— 但**本仓库是自包含的**：
跑起来只需要本目录 + 一个微信容器，不需要另一个仓库。

---

## 九、免责声明

本项目仅供个人学习与技术研究，只应用于**你自己的账号**，请遵守微信与各商户的用户协议。
脚本刷出来的积分**可能被风控清零**，账号也可能受影响 —— 建议用闲置小号，风险自担。

### 关于授权：源码公开，但**不是开源**

完整条款见 [LICENSE](LICENSE)：**保留所有权利**，仅允许阅读、学习和给自己的账号使用；
禁止商用、禁止再分发（尤其禁止并入各种「脚本合集」再发布）。

为什么不给 MIT 这类开源许可：

1. **给不了。** 本项目运行依赖上游 WMPFDebugger（GPL-2.0），拿别人的 GPL 衍生代码去发
   MIT，是在授予自己并不拥有的权利。
2. **不该给。** 这是个 hook 微信客户端、自动领积分的工具。宽松许可等于主动鼓励别人
   打包分发 —— 被并进「签到合集」放大的后果，比不授权大得多。
   保留所有权利是最贴合现状的姿态：**你可以看，但不能拿去发。**

**边界（已核过一遍，2026-09-26）**：仓库里唯一与上游有版权关系的是 `wmpf/` 目录
—— 那是给 WMPFDebugger 打补丁、算偏移用的工具，**已经清干净了**：

- 上游那三份偏移配置（`addresses.{14910,14978,25665}.json`）**已从仓库移除** ——
  留着等于分发上游文件副本；用的人装一次上游就有（[DEPLOY.md 第 2.4 节](docs/DEPLOY.md#24-先验-wmpf-版本不通过就别往下走)）。
  仓库里只剩 `11459` / `14664` 两份，那是**我们自己的算式跑出来的**产出。
- `hook_patch.sh` 原来内嵌了上游 `hook.js` 的两行源码字面量，**已改成结构无关的插入式改写**
  （靠数组形态 / `includes` 调用形态定位，不引用上游的具体数字与变量名）——
  顺带让补丁对上游改写更鲁棒。
- `offsets/recover_offsets.py` 里「照搬 / 对齐上游实现」的措辞已中性化。
  代码本身是 528 行纯 Python、**没有一行上游 JS**，措辞只是描述判据来源。

其余全部自研：主引擎是**进程外**通过 `docker exec` 调上游（`/opt/wmpf` 在 hook 容器里），
属正常使用而非衍生作品。所以「保留所有权利」对**本仓库自己这部分**是成立的。
