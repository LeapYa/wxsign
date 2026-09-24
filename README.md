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
| 来菜 | 来菜 | `wx944ea5f5f7c3dc1f` | 【26】每日签到，半价吃招牌菜 | ⏳ 已接入，未实跑 |
| 九村烤脑花 | 九村烤脑花YX | `wx24f657cf389aa2ad` | 26年会员签到 | ⏳ 已接入，未实跑 |
| 酒煮江湖 | 积膳餐饮游戏 | `wx5fb9d9352a88e693` | 酒煮江湖日常积签到活动 | ⏳ 已接入，未实跑 |
| 蜀大侠 | 蜀大侠活动号 | `wx23e20185d7551afc` | 周三会员日赢大奖（大转盘） | ⏳ 已接入，未实跑 |
| 农耕记 | 农耕记转盘 | `wx3e0d5efb7c8e0e2d` | 积分秒杀菜品券（大转盘） | ⏳ 已接入，未实跑 |
| 肉串汪 | 肉串汪活动入口 | `wxba723ab49cb3e098` | 同款活动壳 | ⏳ 已接入，未实跑 |
| 谷连天 | 谷连天+ | `wxe2d7c8cb0987ded6` | 同款活动壳 | ⏳ 已接入，未实跑 |
| 麻辣空间 | 麻辣空间趣玩 | `wx27b4fa038bc45638` | 同款活动壳 | ⏳ 已接入，未实跑 |

> **「状态」是什么意思**：这 9 个的**鉴别**（确认是吾享 + 带签到/活动能力）和**接入**
> （appId、载体类型、gameId 都填进 `brands.json`）**全部完成**。
> 差别只在最后一环 —— **实际签过一次**：只有辣可可是真机跑过的（引擎就是拿它验证的），
> 其余 8 个是同一套引擎、代码已就绪，但还没跑过首次运行。
>
> 首次运行需要**该微信账号在各家是会员**（辣可可账号在别家不是），这一步脚本会**自动注册**
> （前提是配了 `WXSIGN_REGISTER_PHONE`）。另外要说明的是：**你不需要手工"抓身份"** ——
> `mpId / openId / memberId / 卡号 / token` 这些都由脚本在首次运行时**自己**从微信里读出来
> （开小程序 → CDP 读登录态 + `wx.login` 换 token）。跑一次 `python3 wxsign.py <slug> --ensure` 即可。

> 签到给的一般是**会员积分**，攒着到小程序【商城】兑换。
> 以辣可可为例：每天 +1 积分，连续 10 / 20 / 30 天分别加赠 10 / 15 / 20 积分，积分有效期 365 天。

另外还有一批**别的服务商**的小程序也有签到（呷哺呷哺、五条友、竹园村、望京小腰、李先生、
刘一手、大渔铁板烧、大龙燚等），但那不是吾享体系、用的是另一套接口，本合集目前不覆盖。

---

## 二、跑起来

### 前提

青龙、微信容器、hook 容器必须在**同一台宿主机**上 —— 青龙要调宿主 docker 去操作微信容器
（token 靠微信客户端当场产生，服务端没法自己生成）。

从零怎么搭（装 Docker、起微信容器、验 WMPF 版本、挂 hook、打补丁、建青龙）
见 **[docs/DEPLOY.md](docs/DEPLOY.md)**，本仓库自带，一次配好就不用再动。

### 青龙环境变量

```
WOC_INSTANCE=woc-wx-xxxxxxxx      # 微信实例容器名
WOC_HOOK=woc-hook                 # 旁挂 hook 容器名
WXSIGN_PYTHON=/usr/bin/python3
WXSIGN_HOME_CONTAINER=/ql/data/scripts/wxsign   # 脚本在容器里的路径
WXSIGN_REGISTER_PHONE=13800000000   # 可选：不是会员时自动注册用
```

> 打开小程序用的 `wxopen.py`、读 appId 用的 `wxcdp.py` 等都在仓库里，
> 引擎每次跑会**自动投递**到微信容器，不需要额外配置路径。

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
python3 wxsign.py --find <关键词>      # 自动搜出该品牌名下的小程序并读出 appId（不用手抄）
python3 wxsign.py <slug> --probe      # 只探测（会员状态 + 活动列表），不签到
python3 wxsign.py <slug> --discover   # 打活动原始 JSON（接入新品牌时用来找 gameId）
python3 wxsign.py <slug> --register   # 只注册会员
python3 wxsign.py <slug>              # 该品牌签到（不是会员会自动注册再签）
python3 wxsign.py <slug> --ensure     # 先自动开小程序 + 刷 token，再签到
python3 wxsign.py --all --ensure      # 所有启用的品牌
```

`--find` 干的就是你会手工做的事：打开小程序面板 → 搜索关键词 → 逐张结果卡片点开 →
从逻辑层读出 appId（比窗口标题硬）→ 再检查每个号的小程序包，得出**是不是吾享、
属于哪类载体、有没有签到能力**，最后直接给一段可以粘进 `brands.json` 的配置。

```
发现 4 个号：
  #    小程序名            appId                供应商   载体    签到能力
  1    许家CUISINE        wx4a754a0f6e06c359   吾享     other  无
  2    许家菜甄选           wx05d403af04751c58   吾享     other  无
  3    徐春娇下饭菜          wx36ad9b2c702c291b   其他     非吾享    有签到页
  4    中牟迅才招聘网         wx93882413f14840d4   其他     非吾享    无
```

每个品牌收尾都会打印一行，方便在青龙日志里 grep：

```
RESULT lakeke code=415 msg=今日已签到
```

---

## 四、目录

```
wxsign/
├── wxsign.py            引擎：发现 appId / 探测 / 注册 / 签到 / 批量（青龙直接调它）
├── sign.sh              青龙总入口：自检 → 掉登录就点登录（**要手机确认**）→ 逐品牌签到
├── brands.json          品牌表（品牌名 / 小程序名 / appId / gameId / 载体类型）
├── brands/              每品牌凭证（自动生成，含 token，**别提交**）
│   └── _template.env
├── docs/
│   └── DEPLOY.md        从零部署：微信容器 + hook + 青龙 + 微信侧故障处理
├── wxopen.py            （容器内跑）开指定小程序；窗口/点击/安全关窗原语都在这里
├── wxfind.py            （容器内跑）按关键词搜小程序并读出 appId
├── wxident.js           （hook 容器内跑）身份采集，按 appId + mpId 挑上下文
├── wxrefresh.js         （hook 容器内跑）token 刷新，wx.login 换短效 JWT
├── wxcdp.py             （容器内跑）纯标准库 WebSocket + CDP，读小程序 appId
├── pkgprobe.py          （容器内跑）拆小程序包判供应商 / 签到能力
├── wxclean.py           （容器内跑）清残留小程序窗口（弹窗挡住关闭按钮时先遣散再关）
├── wxlogin.py           （容器内跑）点登录 → **等手机确认** → 拉全屏；过期则提示扫码
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
# 1) 一条命令把 appId 找出来（它自己开面板搜、点开卡片、读 appId、判包）
python3 wxsign.py --find <品牌关键词>
#    把它打印的 JSON 粘进 brands.json 的 brands 数组（slug 自己改成英文）

# 2) 跑一次 —— 它会自己开小程序、抓身份（mpId/openId/memberId/卡号）、刷 token
python3 wxsign.py <slug> --ensure

# 3) 看活动：lot 型会自动从活动列表里认出签到类活动并写入 gameId；
#    sign 型没有列举接口，需要进一次签到页看活动 id，填进 brands.json 的 gameid
python3 wxsign.py <slug> --probe
python3 wxsign.py <slug> --discover

# 4) 联调
python3 wxsign.py <slug>
```

**除了给 `brands.json` 加一条（appId 由 `--find` 自动读出、gameId 见第 3 步），其余全自动。**
（上面 9 个品牌的 appId 和辣可可的 gameId 都已经在 `brands.json` 里了，所以现在也是零手工。）

---

## 七、已知限制

1. **登录是一次性配置，但要留意「别让它掉」。**
   首次扫码登录一次（任何方案都躲不开这一步），之后只要**不重启**容器/宿主就一直有效、
   运行期不需要任何人工。⚠️ 一旦因重启掉登录：点完「登录」**必须在手机上确认**
   （Linux 版客户端没有 Windows 那个「登录免确认」选项），手机久不确认还会提示
   「登录状态已过期」→ 之后只能扫码。`sign.sh` 遇到这两种情况会以退出码 `2` / `3`
   **明确中止**，不会假装成功往下跑。运维要点就一条：**容器与宿主都别重启**。
2. **青龙与微信必须同机** —— token 靠微信客户端产生，服务端自举不了。
   除此之外**零手工**：身份、token、会员信息、活动 id 都由脚本自己取。
3. **`lot` 型的「签到」参数结构还没真机验证**。`/api/game/lot/check` 存在、包裹体已知，
   但它要的 `data` 字段还没在生产上跑通。现有实现属于尽力而为 ——
   建议先用 `--probe` / `--discover` 看清活动结构，再开定时任务。
4. **`sign` 型的活动 id 只能进一次签到页拿**。`gameId` 不在任何接口或小程序包里
   （`/api/game/sign/*` 和 `/api/game/lot/list` 都要求先给 id，没有列举接口），
   所以得人工进一次签到页。不过它是**长期常量**（辣可可那个到 2028-01），
   填进 `brands.json` 的 `gameid` 就永久有效 —— 已经填好了，所以现在也是零手工。
5. **同一时刻只能开一个小程序** → `--all` 是串行的，每个品牌约 1~2 分钟。
6. **注册分支只单测过**（无手机号 / 格式错 / 落 UI 三种都按预期拦住），
   没有真发过一次注册请求 —— 那会在你账号上真实建会员。填好手机号后跑一次
   `wxsign.py <slug> --register` 才算验证过。
7. **风控**：在非官方环境运行微信本身违反其条款，建议用闲置小号，先跑一两周再定型。
   服务商侧也留了口子 —— 辣可可的签到规则里明确写「非正当手段获得的积分会被清零」。

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

> ⚠️ 唯一要留意的：**别让微信掉登录** —— 一旦因重启掉登录，恢复时需要在手机上确认一次
> （登录本身是一次性配置，见[第七节](#七已知限制)第 1 条）。
