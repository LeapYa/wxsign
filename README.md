# 微信小程序签到合集

微信小程序的每日签到脚本合集，青龙面板 / cron 都能跑。

目前覆盖 **吾享（wuuxiang）** 系 —— 天财商龙旗下的餐饮 SaaS。
辣可可、农耕记、蜀大侠、来菜、九村烤脑花、酒煮江湖、肉串汪、谷连天、麻辣空间
用的都是它，所以**一套脚本就能全签**。

> **上手就三步**：把 `wxsign/` 放进青龙脚本目录 → 配几个环境变量 → 加一条定时任务
> `bash /ql/data/scripts/wxsign/sign.sh`。
>
> **不需要手工抓任何东西。** 开小程序、刷 token、抓身份（`openId / mpId / memberId / 卡号`）、
> 不是会员时注册、签到 —— 全是自动的，第一次跑和以后跑走同一条路。
> 唯一的输入是注册要用的**手机号**（配一次，所有品牌共用）。

---

## 一、能签到的小程序（9 个）

| 品牌 | 小程序 | appId | 首页活动 | 状态 |
|---|---|---|---|---|
| 辣可可 | 辣可可现炒黄牛肉i | `wxf8a17a14c0521576` | 可可会员签到（每天 1 积分） | ✅ **已真机跑通** |
| 来菜 | 来菜 | `wx944ea5f5f7c3dc1f` | 【26】每日签到，半价吃招牌菜 | 已接入，待抓身份 |
| 九村烤脑花 | 九村烤脑花YX | `wx24f657cf389aa2ad` | 26年会员签到 | 已接入，待抓身份 |
| 酒煮江湖 | 积膳餐饮游戏 | `wx5fb9d9352a88e693` | 酒煮江湖日常积签到活动 | 已接入，待抓身份 |
| 蜀大侠 | 蜀大侠活动号 | `wx23e20185d7551afc` | 周三会员日赢大奖（大转盘） | 已接入，待抓身份 |
| 农耕记 | 农耕记转盘 | `wx3e0d5efb7c8e0e2d` | 积分秒杀菜品券（大转盘） | 已接入，待抓身份 |
| 肉串汪 | 肉串汪活动入口 | `wxba723ab49cb3e098` | 同款活动壳 | 已接入，待抓身份 |
| 谷连天 | 谷连天+ | `wxe2d7c8cb0987ded6` | 同款活动壳 | 已接入，待抓身份 |
| 麻辣空间 | 麻辣空间趣玩 | `wx27b4fa038bc45638` | 同款活动壳 | 已接入，待抓身份 |

> 签到给的一般是**会员积分**，攒着到小程序【商城】兑换。
> 以辣可可为例：每天 +1 积分，连续 10 / 20 / 30 天分别加赠 10 / 15 / 20 积分，积分有效期 365 天。

另外还有一批**别的服务商**的小程序也有签到（呷哺呷哺、五条友、竹园村、望京小腰、李先生、
刘一手、大渔铁板烧、大龙燚等），但那不是吾享体系、用的是另一套接口，本合集目前不覆盖。

---

## 二、跑起来

### 前提

青龙与微信容器必须在**同一台宿主机**上 —— 青龙要调宿主 docker 去操作微信容器
（token 靠微信客户端产生，服务端没法自己生成）。

微信容器用[云微 WechatOnCloud](https://github.com/Gloridust/WechatOnCloud) 起，
再按辣可可项目（`lakeke-sign`）的 `DEPLOY-QINGLONG.md` 挂好 `woc-hook`
（WMPFDebugger 旁挂容器，用来抓 jsCode）。这部分比较长，一次配好就不用再动。

### 青龙环境变量

```
WOC_INSTANCE=woc-wx-xxxxxxxx      # 微信实例容器名
WOC_HOOK=woc-hook                 # 旁挂 hook 容器名
WXSIGN_PYTHON=/usr/bin/python3
WXSIGN_HOME_CONTAINER=/ql/data/scripts/wxsign    # 脚本在容器里的路径
WXSIGN_MINIAPP_PY=/ql/data/scripts/lakeke-sign/reopen_miniapp.py   # 复用辣可可那份开小程序脚本
LAKEKE_HOOK_DIR=/ql/data/scripts/lakeke-sign                       # 复用它的 hook_up.sh / wxlogin.py
WXSIGN_REGISTER_PHONE=13800000000   # 可选：不是会员时自动注册用
```

### 青龙定时任务

| 名称 | 命令 | 定时 |
|---|---|---|
| 小程序签到合集 | `bash /ql/data/scripts/wxsign/sign.sh` | `10 8 * * *` |
| 保活（可选） | `bash /ql/data/scripts/wxsign/sign.sh --ensure-only` | `0 */2 * * *` |

跑完会**关掉小程序省内存**，下次签到自己重开。

### 依赖

**不需要装任何东西。** Python 侧只用标准库；JS 侧跑在 `woc-hook` 里用它自带的 node。

---

## 三、常用命令

```bash
python3 wxsign.py --list              # 看品牌表
python3 wxsign.py <slug> --probe      # 只探测（会员状态 + 活动列表），不签到
python3 wxsign.py <slug> --discover   # 打活动原始 JSON（接入新品牌时用来找 gameId）
python3 wxsign.py <slug> --register   # 只注册会员
python3 wxsign.py <slug>              # 该品牌签到（不是会员会自动注册再签）
python3 wxsign.py <slug> --ensure     # 先自动开小程序 + 刷 token，再签到
python3 wxsign.py --all --ensure      # 所有启用的品牌
```

每个品牌收尾都会打印一行，方便在青龙日志里 grep：

```
RESULT lakeke code=415 msg=今日已签到
```

---

## 四、目录

```
wxsign/
├── wxsign.py            引擎：探测 / 注册 / 签到 / 批量（青龙直接调它）
├── sign.sh              青龙总入口：自检 → 掉登录就点登录 → 逐品牌签到
├── wxclean.py           清残留小程序窗口（弹窗挡住关闭按钮时先遣散再关）
├── wxident.js           身份采集（hook 容器内跑，按 appId + mpId 挑上下文）
├── wxrefresh.js         token 刷新（hook 容器内跑，wx.login 换短效 JWT）
├── brands.json          品牌表（静态：品牌名 / 小程序名 / appId / 载体类型）
├── brands/              每品牌凭证（自动生成，含 token，**别提交**）
│   └── _template.env
└── README.md
```

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

### 两类「载体」小程序

| 类型 | 包内特征 | 承载 |
|---|---|---|
| `sign` | 有 `/api/game/sign/{detail,monthDetail,signIn}` | 专用签到模块（只有辣可可现炒黄牛肉i 属此类） |
| `lot` | 只有 `/api/game/lot/{list,detail,check,prize/*}` | 活动壳：**大转盘 / 抽奖 + 签到是其中一种活动类型** |

`lot` 型的签到走「活动」：`lot/list` 拿活动列表（就是首页那些卡片），`lot/check` 参与。

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

### 不是会员怎么办

签到接口要 `memberId / cardId / cardNo`，只有会员才有。
不是会员时脚本会自动走注册（照抄小程序页面源码 `registerVip` 的入参）：

```
POST /api/member/register   data={mobile, gameId, thirdShopId, byInviteCode}
```

`mobile` 是明文手机号。同一个人可以在多个品牌各注册一次会员，互不影响。

---

## 六、接入一个新品牌

```bash
# 1) 在 brands.json 里加一条（slug / name / appid / keyword / miniapp / carrier）
#    不知道 appid 就先在小程序面板搜到它的「活动号」，拆包看域名确认是不是吾享

# 2) 跑一次 —— 它会自己开小程序、抓身份（mpId/openId/memberId/卡号）、刷 token
python3 wxsign.py <slug> --ensure

# 3) 看活动：lot 型会自动从活动列表里认出签到类活动并写入 gameId；
#    sign 型没有列举接口，需要进一次签到页看活动 id，填进 brands.json 的 gameid
python3 wxsign.py <slug> --probe
python3 wxsign.py <slug> --discover

# 4) 联调
python3 wxsign.py <slug>
```

**除了第 1 步要填 appId、以及 sign 型要填 gameId，其余全自动。**
（上面 9 个品牌的 appId 和辣可可的 gameId 都已经在 `brands.json` 里了。）

---

## 七、已知限制

1. **青龙与微信必须同机** —— token 靠微信客户端产生，服务端自举不了。
   除此之外**零手工**：身份、token、会员信息、活动 id 都由脚本自己取。
2. **`lot` 型的「签到」参数结构还没真机验证**。`/api/game/lot/check` 存在、包裹体已知，
   但它要的 `data` 字段还没在生产上跑通。现有实现属于尽力而为 ——
   建议先用 `--probe` / `--discover` 看清活动结构，再开定时任务。
3. **`sign` 型的活动 id 只能进一次签到页拿**。`gameId` 不在任何接口或小程序包里
   （`/api/game/sign/*` 和 `/api/game/lot/list` 都要求先给 id，没有列举接口），
   所以得人工进一次签到页。不过它是**长期常量**（辣可可那个到 2028-01），
   填进 `brands.json` 的 `gameid` 就永久有效 —— 已经填好了，所以现在也是零手工。
4. **同一时刻只能开一个小程序** → `--all` 是串行的，每个品牌约 1~2 分钟。
5. **注册分支只单测过**（无手机号 / 格式错 / 落 UI 三种都按预期拦住），
   没有真发过一次注册请求 —— 那会在你账号上真实建会员。填好手机号后跑一次
   `wxsign.py <slug> --register` 才算验证过。
6. **风控**：在非官方环境运行微信本身违反其条款，建议用闲置小号，先跑一两周再定型。
   服务商侧也留了口子 —— 辣可可的签到规则里明确写「非正当手段获得的积分会被清零」。

---

## 八、相关

- 本仓库是从 **辣可可自动签到**（`lakeke-sign`）泛化出来的：
  `wxident.js` / `wxrefresh.js` 是那边 `cdp_lakeke_ident.js` / `auth_refresh_node.js` 的去品牌化版本，
  「打开指定小程序」复用那边的 `reopen_miniapp.py`。
- 微信容器 + hook 的运维细节（WMPF 版本、场景号白名单、登录掉了怎么救）
  见辣可可项目的 `DEPLOY-QINGLONG.md`。
