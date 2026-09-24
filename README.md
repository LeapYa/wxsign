# 吾享（wuuxiang）签到 · 活动合集

> 面向**青龙面板**的吾享系微信小程序签到集合。
> 吾享 = 天财商龙旗下餐饮 SaaS（`wechat.wuuxiang.com` / `scrm.wuuxiang.com`），
> 辣可可、农耕记、蜀大侠、来菜、九村烤脑花、酒煮江湖、肉串汪等餐饮品牌都是它的租户。

## 一、为什么是「一个引擎 + N 份配置」而不是 N 套脚本

实测确认（拆包 + 真机联调）：

1. **吾享全家共用同一套后端**，所有业务接口都是
   `POST https://scrm.wuuxiang.com/crm7game-api/api/<path>`，
   包裹体统一为 `{"mpId":…, "openId":…, "unionId":…, "data":{…}}`，
   鉴权头 `Authorization: <token>` + `crm7-mpId: <mpId>`（可选 `csl-GC-Shardingkey`）。
   换品牌只换**参数**，不换协议。
2. **唯一的品牌差异是四个值**：`appId`（哪个小程序）、`mpId`（哪个租户）、
   `gameId`（哪个活动）、以及账号在该租户下的会员身份。
3. 所以合集 = `wxsign.py`（引擎，通用）+ `brands/<slug>.env`（每品牌一份凭证）。

### 已鉴别出的「载体小程序」类型

| carrier | 包内特征 | 承载 |
|---|---|---|
| `sign` | 含 `/api/game/sign/{detail,monthDetail,signIn}` | 专用签到模块（辣可可现炒黄牛肉i 属此类，**已真机联调**） |
| `lot` | 只含 `/api/game/lot/{list,detail,check,prize/*}` | 活动壳：**大转盘/抽奖 + 签到是其中一种活动类型** |

> 关键发现：`lot` 型号**没有** `/api/game/sign/*` 这 5 个接口，签到是作为「活动」跑的。
> 通用入口是 `/api/game/lot/list`（拿活动列表，即首页那些卡片）与 `/api/game/lot/check`（参与）。

## 二、目录

```
wxsign/
├── wxsign.py            引擎：探测 / 注册 / 签到 / 批量（青龙直接调它）
├── sign.sh              青龙总入口：自检 → 掉登录就点登录 → 逐品牌签到
├── wxclean.py           清残留小程序窗口（弹窗挡住关闭按钮时先遣散再关）
├── wxident.js           身份采集（hook 容器内跑，按 appId+mpId 挑上下文）
├── wxrefresh.js         token 刷新（hook 容器内跑，wx.login 换短效 JWT）
├── brands.json          品牌表（静态：品牌名/小程序名/appId/carrier）
├── brands/              每品牌凭证（自动生成，含 token，**别提交**）
│   └── _template.env
└── README.md
```

## 三、青龙上跑起来（前提：青龙与微信容器同一台宿主机）

青龙必须能操作宿主 docker —— 即挂 `-v /var/run/docker.sock:/var/run/docker.sock`
与 `-v $(which docker):/usr/bin/docker`，脚本目录挂到 `~/ql/data/scripts`。

```bash
# 1) 把本目录放到脚本目录
cp -r wxsign /root/ql/data/scripts/

# 2) hook 容器要把脚本目录挂进 /work（牌子是 wxsign.py 里默认的 HOME=/work 之外，
#    所以用 WXSIGN_HOME 指到容器内路径；青龙里配环境变量即可）
docker run -d --name woc-hook --pid=container:$WX --network=container:$WX \
  --cap-add=SYS_PTRACE --security-opt seccomp=unconfined \
  -v /root/ql/data/scripts:/work -v woc-data-xxxx:/config:ro \
  woc-hook:1 sleep infinity
```

青龙「环境变量」页配置：

```
WOC_INSTANCE=woc-wx-2ada0225ca
WOC_HOOK=woc-hook
WXSIGN_PYTHON=/usr/bin/python3
WXSIGN_MINIAPP_PY=/root/ql/data/scripts/lakeke-sign/reopen_miniapp.py
LAKEKE_HOOK_DIR=/root/ql/data/scripts/lakeke-sign
```

青龙「定时任务」：

| 名称 | 命令 | 定时 |
|---|---|---|
| 吾享签到合集 | `bash /ql/data/scripts/wxsign/sign.sh` | `10 8 * * *` |
| 吾享保活（可选） | `bash /ql/data/scripts/wxsign/sign.sh --ensure-only` | `0 */2 * * *` |

**依赖**：只用 Python 标准库；JS 侧跑在 woc-hook 里用它自己的 node（`NODE_PATH=/opt/wmpf/node_modules`）。

## 四、接入一个新品牌（5 步）

```bash
cd wxsign

# 1) 在 brands.json 里加一条（slug / name / appid / keyword / miniapp / carrier）
#    不知道 appid 就先在小程序面板搜到它的“活动号”，用 survey 工具链拿 appId

# 2) 人工把这个品牌的活动小程序在微信里打开一次（或让 sign.sh --ensure 自动开）

# 3) 抓身份（appId 填第 1 步的）
docker exec woc-hook sh -c 'cd /work/wxsign && ENVFILE=/work/wxsign/brands/<slug>.env \
  WX_APPID=<appid> NODE_PATH=/opt/wmpf/node_modules node wxident.js 60'

# 4) 探测：确认账号是不是该品牌会员、活动列表长什么样
python3 wxsign.py <slug> --probe
python3 wxsign.py <slug> --discover      # 打活动原始 JSON，用来填 WX_GAMEID

# 4.5) 不是会员？自动注册（要先配好手机号）
export WXSIGN_REGISTER_PHONE=13800000000   # 或用 brands/<slug>.env 的 WX_REGISTER_PHONE
python3 wxsign.py <slug> --register

# 5) 联调签到
python3 wxsign.py <slug>
```

`--probe` / `--discover` 的输出就是接入文档：活动列表里挑出签到类的 `gameId`，
写进 `brands/<slug>.env` 的 `WX_GAMEID` 即可。

## 五、已鉴别的候选品牌（来自本轮批量鉴别）

| 品牌 | 载体小程序 | appId | carrier | 首页可见活动 |
|---|---|---|---|---|
| 辣可可 | 辣可可现炒黄牛肉i | `wxf8a17a14c0521576` | sign | 每日积分签到（**已联调**） |
| 蜀大侠 | 蜀大侠活动号 | `wx23e20185d7551afc` | lot | 周三会员日赢大奖 · 幸运抽奖（大转盘） |
| 九村烤脑花 | 九村烤脑花YX | `wx24f657cf389aa2ad` | lot | 26年会员签到 · 打卡签到 |
| 农耕记 | 农耕记转盘 | `wx3e0d5efb7c8e0e2d` | lot | 积分秒杀菜品券 · 幸运抽奖 |
| 来菜 | 来菜 | `wx944ea5f5f7c3dc1f` | lot | 【26】每日签到，半价吃招牌菜 |
| 酒煮江湖 | 积膳餐饮游戏 | `wx5fb9d9352a88e693` | lot | 酒煮江湖日常积签到活动 |
| 肉串汪 | 肉串汪活动入口 | `wxba723ab49cb3e098` | lot | 活动列表（加载较慢） |
| 许家菜 / 巡湘记 / 失眠常德 | 各自的会员卡型号 | 见 `brands.json` | lot | 未见签到，`enabled:false` 备用 |

## 六、已知限制 / 待联调（**重要，别当已完成**）

1. ~~账号必须先是该品牌的会员~~ → **已自动化**。签到接口要 `memberId/cardId/cardNo`，只有会员才有；
   不是会员时脚本会自己走注册（`POST /api/member/register`，明文手机号，该接口在每个吾享游戏型包里都有）。
   手机号配 `WX_REGISTER_PHONE`（单品牌）或青龙环境变量 `WXSIGN_REGISTER_PHONE`（所有品牌共用）。
   同一个人可以在多个品牌各注册一次会员，互不影响。
   > ⚠️ 注册分支的接线已单测过（无号/格式错/落 UI 三种都按预期拦住），但**没有真的打过一次注册请求**——
   > 那会在你账号上真实建会员，需要你自己填手机号后跑 `wxsign.py <slug> --register` 验证。
2. **`lot` 型的「签到」动作参数结构尚未真机联调**。`/api/game/lot/check` 已知存在、
   包裹体已知，但它要的 `data` 字段（是否要 `gameId` + `activityId` + 更多）还没在真机上验证过。
   现有实现是**尽力而为**，请先用 `--probe` / `--discover` 看清活动结构再开定时任务。
3. **token 是短效 JWT（实测 ~110 分钟）**，每次签到前都要在**那个 appId 的小程序开着**的
   前提下 `wx.login()` 换新。所以**青龙必须与微信容器同机**，无法纯服务端自举。
4. 同一时刻只能开一个小程序 → `--all` 是**串行**的，每个品牌约 1~2 分钟，7 个品牌约 10 分钟。
5. 风控：在非官方环境运行微信本身违反其条款，**用闲置小号**，别用主力号；先跑一两周再定型。

## 七、与辣可可那个项目的关系

本目录是从 `lakeke-sign`（辣可可自动签到）泛化出来的：
- 引擎逻辑（包裹体、响应码约定、token 刷新链路）沿用并已在其上验证；
- `wxident.js` / `wxrefresh.js` 是 `cdp_lakeke_ident.js` / `auth_refresh_node.js` 的**去品牌化**版本
  （appId / mpId 改从环境变量来，env 前缀 `LAKEKE_` → `WX_`）；
- 「打开指定小程序」仍复用 `reopen_miniapp.py`（它已支持 `LAKEKE_MINIAPP` / `LAKEKE_KEYWORD` 环境变量）；
- hook 补丁、微信容器运维见 `wechat-linux-container-ops` skill 与辣可可项目的 `DEPLOY-QINGLONG.md`。
