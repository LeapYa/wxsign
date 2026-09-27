#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""吾享（wuuxiang / 天财商龙 CRM7）小程序「签到·活动」合集引擎 —— 青龙面板可用。

为什么一个脚本能覆盖所有吾享品牌（实测确认）：
    吾享全家共用同一套后端、同一套包裹体 ——
      POST https://scrm.wuuxiang.com/crm7game-api/api/<path>
      body   {"mpId":..., "openId":..., "unionId":..., "data":{...}}
      header Authorization: <token>   crm7-mpId: <mpId>   [csl-GC-Shardingkey: <gcId>]
    所以不需要「每品牌一套脚本」，只要**一个引擎 + 每品牌一份配置**。

包内能力实测（决定用哪套接口）：
    sign 型号包里有 /api/game/sign/{detail,monthDetail,signIn}  —— 辣可可现炒黄牛肉i 属此类
    lot  型号包里只有 /api/game/lot/{list,detail,check,prize/get,prize/notify}
                              —— 大转盘/抽奖活动壳，签到属于其中一种活动类型

用法（在青龙「定时任务」里写命令）：
    python3 <脚本目录>/wxsign.py --list              看品牌表 + 本次生效范围
    python3 <脚本目录>/wxsign.py <slug> --probe      只探测（会员 + 活动列表），不签到
    python3 <脚本目录>/wxsign.py <slug> --discover   把该租户所有活动的原始 JSON 打出来
    python3 <脚本目录>/wxsign.py <slug> --register   只做会员注册（不是会员时用）
    python3 <脚本目录>/wxsign.py <slug>              该品牌签到（不是会员会自动注册再签）
    python3 <脚本目录>/wxsign.py --all [--ensure]    所有 enabled 品牌（--ensure 会先开小程序并刷 token）

签哪几个（可选，默认 all）—— 详见 resolve_targets()：
    --apps a,b,c / WXSIGN_APPS=a,b,c        白名单：只签这几个（逗号分隔的多选；写 all / * 等于不限制）
    --exclude a,b / WXSIGN_EXCLUDE=a,b      黑名单：永不签这几个（优先级最高，白名单/--all 都排除得掉）
    都不配 = all（brands.json 里 enabled 的品牌）。位置参数（`wxsign.py a b`）等同白名单，
    且盖过 WXSIGN_APPS —— 命令行是当场意图，比环境变量优先。

会员注册：签到接口要求 memberId/cardId/cardNo，只有会员才有。不是会员时脚本会**自动注册**
    （POST /api/member/register，明文手机号；该接口在每个吾享游戏型包里都有）。
    手机号来源：brands/<slug>.env 的 WX_REGISTER_PHONE，或进程环境变量 WXSIGN_REGISTER_PHONE。
    同一个人可以在多个品牌各注册一次会员，互不影响。

环境变量：
    WOC_HOOK=woc-hook           旁挂 hook 容器名（刷 token 用）
    WOC_INSTANCE=woc-wx-...     微信实例容器名（自动开小程序用）
    WXSIGN_HOME                 配置根目录，默认本脚本所在目录
    WXSIGN_NOTIFY_PY            可选：notify.py 绝对路径（复用多渠道通知）
    WXSIGN_PYTHON               容器内 python3 路径，默认 python3
    WXSIGN_APPS                 可选：只签这几个（slug，逗号分隔，可多选；默认 all）
    WXSIGN_EXCLUDE              可选：不签这几个（slug，逗号分隔，可多选；优先级最高）

退出码：0 = 全部成功（含「今日已签到」）；1 = 有失败。
每个品牌收尾打印一行便于 grep：  RESULT <slug> code=<业务码> msg=<说明>
"""
import http.client
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.environ.get("WXSIGN_HOME", HERE)
# 同一个目录在宿主机与容器里的挂载点不同（青龙一般是 /ql/data/scripts/... ↔ /work/...），
# 所以「容器内路径」单独一个变量；不设则与 HOME 相同。
HOME_C = os.environ.get("WXSIGN_HOME_CONTAINER", HOME)
# 微信实例容器里放辅助脚本的目录（reopen_miniapp.py / wxclean.py）
CTMP = os.environ.get("WXSIGN_CTMP", "/tmp")


def cpath(*parts):
    """拼**容器内**路径：必须用正斜杠、且保留开头的 /。
    ⚠️ 两个坑都踩过：
      · 用 os.path.join 时，Windows 上会拼出反斜杠（/tmp\\x.py）→ 容器里「找不到文件」（exit 2）
      · 对首段做 strip('/') → 变成相对路径（work/wxsign/...）→ ENOENT
    """
    return "/".join(str(p).rstrip("/") for p in parts if p not in ("", None))
BRANDS_JSON = os.path.join(HOME, "brands.json")
ENV_DIR = os.path.join(HOME, "brands")

HOOK = os.environ.get("WOC_HOOK", "woc-hook")
INSTANCE = os.environ.get("WOC_INSTANCE", "")
CPY = os.environ.get("WXSIGN_PYTHON", "python3")

CRM_BASE = "https://scrm.wuuxiang.com/crm7game-api"
LOGIN_URL = "https://wechat.wuuxiang.com/i5xforyou/auth/login"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/132.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
      "MiniProgramEnv/Windows WindowsWechat/WMPF XWEB/25715")

_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE

# CRM 接口的**复用连接**（见 _api_post_once 的说明：不复用会临时端口耗尽）
# ⚠️ http.client 要的是「host + 完整路径」两段，别把 CRM_BASE 的路径前缀丢了
#    （丢了会静默变成 HTTP 404，看着像接口不存在）。
_CRM_HOST = CRM_BASE.split("//", 1)[1].split("/", 1)[0]
_CRM_PREFIX = "/" + CRM_BASE.split("//", 1)[1].split("/", 1)[1].strip("/")
_CONN = [None]

# 已验证的响应码约定（辣可可真机实测）
CODE_OK, CODE_ALREADY, CODE_AUTH_BAD, CODE_AUTH_EXP = "200", "415", "208", "211"
CODE_NOT_MEMBER, CODE_CARD_BAD = "401", "402"


def log(*a):
    print(*a, flush=True)


def mask(v):
    s = str(v or "")
    return "%s****%s(%d)" % (s[:4], s[-4:], len(s)) if len(s) > 8 else "*" * len(s)


# ───────────────────────── 配置 ─────────────────────────

def load_brands():
    with open(BRANDS_JSON, encoding="utf-8") as f:
        return json.load(f)


def env_path(slug):
    return os.path.join(ENV_DIR, "%s.env" % slug)


def load_env(slug):
    env = {}
    p = env_path(slug)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def save_env(slug, env):
    os.makedirs(ENV_DIR, exist_ok=True)
    p = env_path(slug)
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join("%s=%s" % (k, v) for k, v in env.items() if v) + "\n")
    os.chmod(p, 0o600)


# 绑在**微信账号**上的字段 —— 换登录账号（小号验证完换大号）后必须清掉。
# 为什么不能靠脚本自己更新：
#   · wxrefresh.js 读 env 里的 WX_OPENID 当「目标上下文」去挑 CDP 连接（targetOpen），
#     所以它**会主动去找旧账号**；写回身份时又带 `!env[k]` 守卫 —— 于是旧 openId 一直粘着。
#   · 结果：换号后报「抓不到身份 / invalid code」，而日志看不出跟换号有关。
# 与账号无关、可以保留的：WX_APPID（小程序 id）、WX_MPID（品牌侧 id，同品牌所有人都一样）、
#   WX_GAMEID（活动 id，公开常量）、WX_REGISTER_*（配置项）。
ACCOUNT_FIELDS = ("WX_OPENID", "WX_UNIONID", "WX_TOKEN", "WX_GCID",
                  "WX_MEMBERID", "WX_CARDID", "WX_CARDNO", "WX_THIRDSHOPID")


def reset_identity(slug, dry_run=False):
    """清掉该品牌 env 里所有账号相关字段，保留品牌 / 活动常量。"""
    env = load_env(slug)
    if not env:
        log("  [reset] %s：还没有配置文件，无需清理" % slug)
        return 0
    drop = [k for k in ACCOUNT_FIELDS if env.get(k)]
    keep = {k: v for k, v in env.items() if k not in ACCOUNT_FIELDS}
    log("  [reset] %s：清 %d 个 / 留 %d 个" % (slug, len(drop), len(keep)))
    for k in drop:
        log("      - %s" % k)
    if keep:
        log("      保留：%s" % "、".join(sorted(keep)))
    if dry_run:
        log("      （--dry-run，没有真的写）")
        return len(drop)
    save_env(slug, keep)
    return len(drop)


# ───────────────────── 选哪些小程序（默认 all，可白名单 / 可排除） ─────────────────────

# 这些 flag 后面跟一个值（值**不是**品牌 slug），解析位置参数时要跳过。
_FLAGS_WITH_VALUE = ("apps", "only", "exclude", "skip", "find")


def split_list(v):
    """逗号/分号/空白/中文逗号分隔 → 去重保序的列表。这样 `a,b`、`a b`、`a，b` 都认。"""
    out = []
    for x in re.split(r"[,，;；\s]+", str(v or "")):
        x = x.strip()
        if x and x not in out:
            out.append(x)
    return out


def positional(argv):
    """取位置参数（品牌 slug），跳过 `--flag` 与 `--flag value` 里的那个 value。

    为什么必须跳：`--exclude guliantian` 里的 `guliantian` 不是要签的品牌，
    当成位置参数就会反着来（本想排除，结果变成只签它）。
    """
    out, skip = [], False
    for a in argv:
        if skip:
            skip = False
            continue
        if a.startswith("--"):
            name = a[2:].split("=", 1)[0]
            if name in _FLAGS_WITH_VALUE and "=" not in a:
                skip = True            # 它的下一个 token 是值，不是品牌名
            continue
        out.append(a)
    return out


def pick_opt(argv, flags, envs):
    """取「多选列表」型配置：命令行优先于环境变量。
    支持 `--apps a,b` 与 `--apps=a,b` 两种写法。
    → (列表 或 None, 来源说明)"""
    for f in flags:
        for i, a in enumerate(argv):
            if a == "--" + f:
                return split_list(argv[i + 1] if i + 1 < len(argv) else ""), "--" + f
            if a.startswith("--" + f + "="):
                return split_list(a.split("=", 1)[1]), "--" + f
    for e in envs:
        if os.environ.get(e):
            return split_list(os.environ[e]), e
    return None, ""


def resolve_targets(cfg, argv):
    """决定这次跑哪些品牌。→ (brands 列表, 说明行列表)

    三层，从上到下依次生效：
      ① 默认        → brands.json 里 enabled=true 的全部（这就是「默认 all」）
      ② 白名单      → `--apps a,b` / `WXSIGN_APPS=a,b`（写 all / * 等于不限制）
                      位置参数 `wxsign.py a b` 也算白名单，且**盖过** WXSIGN_APPS
                      —— 命令行是当场意图，比环境变量优先
      ③ 黑名单      → `--exclude a,b` / `WXSIGN_EXCLUDE=a,b`
                      **永远最高优先级**：白名单写进去、`--all` 也会被排除掉。
                      这是刻意的 —— 「这个号我永远不签」应该是一条硬约束。

    白名单 / 黑名单里的 slug 在 brands.json 找不到时只警告、不静默忽略
    （拼错一个字母就什么都不跑、还看不出为什么，很难排查）。
    """
    by_slug = {b["slug"]: b for b in cfg["brands"]}
    all_enabled = [b for b in cfg["brands"] if b.get("enabled")]

    pos = positional(argv)
    if pos:
        # 显式点名的品牌不查 enabled —— 想单跑一个停用的号（比如重新探测）应当允许
        base = [by_slug[s] for s in pos if s in by_slug]
        unknown_want, how = [s for s in pos if s not in by_slug], "命令行指定"
    elif "--all" in argv:
        base, unknown_want, how = list(all_enabled), [], "--all"
    else:
        raw, src = pick_opt(argv, ("apps", "only"), ("WXSIGN_APPS", "WXSIGN_ONLY"))
        if raw and any(x.lower() in ("all", "*") for x in raw):
            raw, src = None, (src + "（写了 all/*，当成不限制）")
        if raw is None:
            base, unknown_want = list(all_enabled), []
            how = src or "默认（brands.json 里 enabled 的品牌）"
        else:
            base = [by_slug[s] for s in raw if s in by_slug]
            unknown_want = [s for s in raw if s not in by_slug]
            how = src

    drops, dhow = pick_opt(argv, ("exclude", "skip"), ("WXSIGN_EXCLUDE", "WXSIGN_SKIP"))
    drops = drops or []
    unknown_drop = [s for s in drops if s not in by_slug]
    kept = [b for b in base if b["slug"] not in drops]
    cut = [b for b in base if b["slug"] in drops]

    lines = ["范围：%s → 选中 %d 个：%s"
             % (how, len(kept), "、".join(b["name"] for b in kept) or "(空)")]
    if cut:
        lines.append("排除：%s → 去掉 %d 个：%s"
                     % (dhow or "WXSIGN_EXCLUDE", len(cut), "、".join(b["name"] for b in cut)))
    elif dhow:
        lines.append("排除：%s 已配，但不在本次范围内" % dhow)
    for s in unknown_want:
        lines.append("⚠️ 白名单里的「%s」不在 brands.json（拼错了？），已忽略" % s)
    for s in unknown_drop:
        lines.append("⚠️ 排除名单里的「%s」不在 brands.json（拼错了？），已忽略" % s)
    return kept, lines


def jwt_exp(tok):
    import base64
    try:
        p = tok.split(".")[1]
        p += "=" * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p)).get("exp")
    except Exception:
        return None


# ───────────────────────── HTTP ─────────────────────────

def api_post(env, path, data, retries=3):
    """带重试的 POST。实测沙箱/容器出口偶发 `502 Bad Gateway`（Tunnel connection failed）
    和 `WinError 10048`（临时端口耗尽），这类是网络层瞬时错误、重试就好，
    不该直接判业务失败 —— 更不该被当成「这个号没有活动」（踩过，见 --probe）。"""
    r = None
    for attempt in range(retries + 1):
        r = _api_post_once(env, path, data)
        if not is_transport_fail(r):
            return r
        if attempt < retries:
            wait = 2.0 * (attempt + 1)
            log("  [net] %s 第 %d 次失败（%s），%.1fs 后重试"
                % (path, attempt + 1, str(r.get("msg"))[:60], wait))
            time.sleep(wait)
    return r


def _api_post_once(env, path, data):
    """单次 POST。**复用同一条 HTTPS 连接**，并在传输错误时小睡重连。

    为什么必须复用：批量跑时这里的调用非常密集，而 urllib.urlopen 每次都新开 socket、
    响应对象一析构就关 —— 高频调用下 Windows 会报
    `WinError 10048 通常每个套接字地址只允许使用一次`（临时端口耗尽 / TIME_WAIT 堆积），
    于是业务请求全返 code=-1，**看起来像「这个号没有活动」，其实是本机网络问题**。
    实测踩过：审计里一个号因此得出假结论（详见 --probe 的传输失败分支）。
    """
    mp = env.get("WX_MPID", "")
    if not mp:
        return {"code": "-1", "msg": "缺少 WX_MPID（先跑 wxident.js 抓身份）", "_transport": 1}
    body = {"mpId": mp, "openId": env.get("WX_OPENID", ""),
            "unionId": env.get("WX_UNIONID", ""), "data": data}
    payload = json.dumps(body).encode()
    headers = {"Content-Type": "application/json",
               "Authorization": env.get("WX_TOKEN", ""),
               "crm7-mpId": mp, "User-Agent": UA,
               "Content-Length": str(len(payload)), "Connection": "keep-alive"}
    if env.get("WX_GCID"):
        headers["csl-GC-Shardingkey"] = env["WX_GCID"]

    last = None
    for attempt in range(3):
        conn = _CONN[0]
        try:
            if conn is None:
                conn = http.client.HTTPSConnection(_CRM_HOST, timeout=20, context=_SSL)
                _CONN[0] = conn
            conn.request("POST", _CRM_PREFIX + path, body=payload, headers=headers)
            r = conn.getresponse()
            raw = r.read().decode(errors="replace")
            if r.status != 200:
                # 服务端明确回了个 HTTP 码 —— 这是「有应答」，不是传输失败
                return {"code": str(r.status), "msg": "HTTP %s: %s" % (r.status, raw[:200])}
            return json.loads(raw)
        except Exception as e:
            last = e
            try:
                conn.close()
            except Exception:
                pass
            _CONN[0] = None
            if attempt < 2:
                time.sleep(0.8 * (attempt + 1))
    return {"code": "-1", "msg": str(last), "_transport": 1}


def is_transport_fail(resp):
    """这个响应是「本机/链路的传输失败」吗（不是业务结论）。"""
    return bool(resp and str(resp.get("code")) == "-1")


def content_of(resp):
    c = resp.get("content")
    if isinstance(c, str):
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            return c
    return c


# ───────────────────────── 微信侧：开小程序 / 刷 token ─────────────────────────

def sh(cmd, timeout=240):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "超时"


def clean_leftovers(hard=False):
    """关掉残留的小程序窗口（含清掉挡住关闭按钮的隐私弹窗）。

    为什么必须做：① 残留窗口会盖住微信侧边栏，`open_panel` 就再也认不出面板；
    ② 更要紧的是 —— **旧窗口里的登录会话会失效**，此时 wx.login() 取到的 code
    拿去 /auth/login 会返 `invalid code`。所以「换不到 token」时的标准动作就是
    「关掉重开」。

    hard=True → 加 `--restart-runtime`，直接杀小程序运行时进程（WeChatAppEx）：
    实测有一类窗口**常规手段根本关不掉** —— 它们是微信小程序面板里的**推广位/推荐卡片**
    （标题像「华夏家博」「永伟美发店」「媚姐养生会所」「潮乐棋牌会馆」），
    点关闭按钮毫无反应。攒到五六个就把侧边栏彻底堵死，之后整批品牌全部打不开面板。
    ⚠️ 硬清之后**必须重启 hook**（WMPFDebugger 只在启动时 attach 一次），这里会自动做。
    """
    if not INSTANCE:
        return False
    args = ["docker", "exec", "-e", "DISPLAY=:1", "-e", "PYTHONUNBUFFERED=1", INSTANCE, CPY, cpath(CTMP, "wxclean.py")]
    if hard:
        args.append("--restart-runtime")
    try:
        c = subprocess.run(args, capture_output=True, text=True, timeout=600)
    except Exception as e:
        log("  [clean] 跳过（%s）" % e)
        return False
    out = (c.stdout or "") + (c.stderr or "")
    killed = False
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("[clean]") or (hard and s.startswith("[reopen]")):
            log("  " + s)
        if "WeChatAppEx" in s and "已杀" in s:
            killed = True
    if killed:
        restart_hook()
    if c.returncode not in (0, 1):
        log("  [clean] rc=%s（不影响后续，继续试）" % c.returncode)
    return c.returncode == 0


WMPF_DIR = os.environ.get("WXSIGN_WMPF_DIR", "/opt/wmpf")


def restart_hook(wait=60):
    """重启 hook 容器里的 WMPFDebugger，等它重新 attach 上来。

    为什么非得重启：它**只在启动时 attach 一次**。硬清杀掉 WeChatAppEx 之后，
    新拉起的小程序实例不会被挂上 —— 症状是 CDP 一个 context 都拿不到（ctx=0）、
    hook 日志里也不再有 `miniapp client connected`，后面每个品牌都会 notoken。
    """
    if not HOOK:
        return False
    log("  [hook] 重启 WMPFDebugger（硬清过小程序运行时，必须重新 attach 一次）")
    kill = ("for p in $(ls /proc | grep -E '^[0-9]+$'); do "
            "[ \"$(cat /proc/$p/comm 2>/dev/null)\" = node ] && kill $p 2>/dev/null; done")
    try:
        subprocess.run(["docker", "exec", HOOK, "sh", "-c", kill],
                       capture_output=True, text=True, timeout=90)
        time.sleep(2)
        # ⚠️ 先把旧日志删掉再启动：否则「日志里有 script loaded」可能是**上一轮**留下的，
        #    就绪判断会在 3 秒内假成功（实测踩过），之后所有刷新都白跑。
        subprocess.run(["docker", "exec", HOOK, "sh", "-c", "rm -f /tmp/wmpf.log"],
                       capture_output=True, text=True, timeout=30)
        subprocess.run(["docker", "exec", "-d", HOOK, "sh", "-c",
                        "cd %s && node node_modules/ts-node/dist/bin.js src/index.ts "
                        "> /tmp/wmpf.log 2>&1" % WMPF_DIR],
                       capture_output=True, text=True, timeout=90)
    except Exception as e:
        log("  [hook] 重启异常：%s" % e)
        return False
    waited = 0
    while waited < wait:
        time.sleep(3)
        waited += 3
        p = subprocess.run(["docker", "exec", HOOK, "grep", "-q", "script loaded", "/tmp/wmpf.log"],
                           capture_output=True, text=True)
        if p.returncode == 0:
            log("  [hook] 就绪（等了 %ds）" % waited)
            return True
    log("  [hook] ⚠️ 等了 %ds 没看到 'script loaded' → 跑 wmpf/check_wmpf.sh 查 WMPF 偏移配置" % wait)
    return False


def leftover_count():
    """现在有几个「关不掉/没关」的小程序窗口？（只数不关，很快）
    用来决定要不要上硬手段 —— 见 ensure_miniapp 里的说明。"""
    if not INSTANCE:
        return 0
    try:
        p = subprocess.run(["docker", "exec", "-e", "DISPLAY=:1", "-e", "PYTHONUNBUFFERED=1", INSTANCE, CPY,
                            cpath(CTMP, "wxclean.py"), "--count"],
                           capture_output=True, text=True, timeout=120)
    except Exception:
        return 0
    for line in ((p.stdout or "") + (p.stderr or "")).splitlines():
        m = re.search(r"\[clean\] LEFT=(\d+)", line.strip())
        if m:
            return int(m.group(1))
    return 0


def _wxopen(brand):
    """开一次小程序，返回 (rc, output)。"""
    args = ["docker", "exec", "-e", "DISPLAY=:1", "-e", "PYTHONUNBUFFERED=1",
            "-e", "WXSIGN_MINIAPP=" + brand["miniapp"],
            "-e", "WXSIGN_KEYWORD=" + brand["keyword"],
            INSTANCE, CPY, cpath(CTMP, "wxopen.py"), "--loose"]
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=400)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        return 1, str(e)


def ensure_miniapp(brand, retry_hard=True):
    """把该品牌的「载体小程序」打开（token 的 jsCode 与 appId 绑死，必须开对号）。"""
    if not INSTANCE:
        log("  [ensure] 未设 WOC_INSTANCE，跳过自动开小程序")
        return False
    clean_leftovers()

    # 用参数列表而非拼字符串：小程序名里有中文，拼命令行容易被引号吃掉
    rc, out = _wxopen(brand)
    # wxopen 的 --loose 语义：0=已打开；4=点过候选但没法用窗口标题确认（交给 appId 复核）；
    # 3=连面板都没打开（几乎总是「侧边栏被残留窗口堵死」）
    ok = rc in (0, 4)

    if not ok and retry_hard:
        # 打不开面板有两大原因，处置完全不同：
        #   ① 侧边栏被**关不掉的推广窗口**堵死 → 只能硬清运行时；
        #   ② 只是 hook / 面板状态不对 → 重启 hook 就够。
        # 而硬清是**有代价的**：小程序面板本身也由 WeChatAppEx 渲染，杀了运行时面板就再也
        # 开不起来（实测踩过：一次硬清 19 个进程，之后每个号都卡在「没能确认小程序面板」）。
        # 所以先花十几秒重启 hook 试一次，**还不行为才动硬的**。
        log("  [ensure] 打开失败（rc=%s）→ 先重启 hook（重新 attach）再试一次" % rc)
        restart_hook()
        rc, out = _wxopen(brand)
        ok = rc in (0, 4)
    if not ok and retry_hard:
        # 硬清**只在真的有残留窗口时才做**。没有残留还硬清 = 白白把面板弄没
        # （实测踩过一次，之后整批号都卡在「没能确认小程序面板」）。
        # 没有残留 → 面板多半是自己那层的问题。wxopen.py 现在会先试 **Ctrl+R 重载面板
        # webview**（实测：面板会掉成「没有连接到网络」的错误页，窗口和标签条都还在，
        # 但里面一个小程序都没有 → CDP 上下文为空。一条 Ctrl+R 就恢复）。
        # 走到这里说明连重载也没成，只能重启微信，所以放弃、别再折腾。
        left = leftover_count()
        if left <= 0:
            log("  [ensure] 重载面板 + 重启 hook 后仍打不开，且**没有残留窗口** → "
                "不是残留堵的；重载也救不回来，多半得重启微信。放弃这个号，不再硬清。")
            return False
        log("  [ensure] 重启 hook 也没打开，但确有 %d 个残留 → 硬清运行时（⚠️ 会连坐面板）" % left)
        clean_leftovers(hard=True)
        rc, out = _wxopen(brand)
        ok = rc in (0, 4)

    log("  [ensure] %s → rc=%s %s" % (brand["miniapp"], rc, "(已就绪)" if ok else "(可能没开成)"))
    for line in out.splitlines():
        # 出错了就把所有输出都打出来 —— 只过滤 [reopen] 会把真正的报错吞掉（踩过）
        if line.startswith("[reopen]") or (not ok and line.strip()):
            log("     " + line[:200])
    if ok:
        agree_privacy()
    return ok


def agree_privacy(rounds=4):
    """把小程序开屏的「隐私保护提示」弹层点掉，让页面真正初始化。

    ⚠️ 这一步是**刷 token 的前置条件**，不点就等于白开：
    小程序在用户同意隐私协议之前**页面根本没初始化** → storage 里没有 mpId →
    wxrefresh 报「拿不到 mpId / invalid code」。实测一整批 53 个号里 80% 死在这，
    日志表现极具误导性：`[ensure] … rc=0 (已就绪)` 紧接着 `[enum] 有 wx 的上下文=[…]`
    每个 ctx 都「拿不到 mpId」—— 看着像 hook 没挂上，其实是弹层没点。
    （`--find` 之所以不受影响：它只读 appId，appId 在逻辑层随时可取，不需要页面初始化。）
    """
    if not INSTANCE:
        return 0
    if os.environ.get("WXSIGN_NO_AGREE", "") == "1":
        return 0
    try:
        p = subprocess.run(["docker", "exec", "-e", "DISPLAY=:1", "-e", "PYTHONUNBUFFERED=1", INSTANCE, CPY,
                            cpath(CTMP, "wxagree.py"), str(rounds)],
                           capture_output=True, text=True, timeout=180)
    except Exception as e:
        log("  [agree] 跳过（%s）" % e)
        return 0
    out = (p.stdout or "") + (p.stderr or "")
    n = 0
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("[agree]"):
            log("  " + s)
            m = re.match(r"\[agree\] 共点 (\d+) 下", s)
            if m:
                n = int(m.group(1))
    return n


def refresh_token(brand, env, force=False):
    """在 hook 容器里调 wx.login 换新 token。token 是短效 JWT，过期只能重取。"""
    tok = env.get("WX_TOKEN", "")
    exp = jwt_exp(tok) if tok else None
    if not force and exp and exp - time.time() > 120:
        log("  [token] 仍有效（剩 %d 分钟），跳过刷新" % ((exp - time.time()) / 60))
        return True
    # ⚠️ 必须用参数列表：在 Windows 上 subprocess(shell=True) 走的是 cmd.exe，
    #    它不认 POSIX 单引号，`sh -c '...'` 会被拆错（实测报 Unterminated quoted string）。
    inner = ("cd %s && ENVFILE=%s WX_APPID=%s WX_MPID=%s "
             "NODE_PATH=/opt/wmpf/node_modules node wxrefresh.js%s"
             % (HOME_C, cpath(HOME_C, "brands", "%s.env" % brand["slug"]),
                brand["appid"], env.get("WX_MPID", ""), " force" if force else ""))
    args = ["docker", "exec", HOOK, "sh", "-c", inner]
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=300)
        rc, out = p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        rc, out = 1, str(e)
    log("  [token] 刷新 rc=%s" % rc)
    for line in out.splitlines():
        if line.strip():
            log("     " + line.strip()[:200])
    return rc == 0


def harvest_ident(brand):
    """抓比 token 刷新更全的身份 —— 尤其是 activity 的 gameId。

    wxrefresh.js 只读 storage 里的 mpId/openId/unionId/gcId；而 wxident.js 还会读
    `getCurrentPages()` 的页面 data，能拿到 baseInfo.gameId（签到活动的 id）。
    辣可可这类 sign 型号的活动 id 不在 /api/game/lot/list 里（那条返回 405），所以只能这样抓。
    自动化的前提是**小程序已经开着**（前一步 ensure_miniapp 刚开过）。
    """
    args = ["docker", "exec",
            "-e", "ENVFILE=" + cpath(HOME_C, "brands", "%s.env" % brand["slug"]),
            "-e", "WX_APPID=" + brand["appid"], HOOK, "sh", "-c",
            "cd %s && NODE_PATH=/opt/wmpf/node_modules node wxident.js 30" % HOME_C]
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=300)
        out = (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        log("  [ident] 异常：%s" % e)
        return False
    log("  [ident] wxident.js rc=%s" % p.returncode)
    for line in out.splitlines():
        if line.strip():
            log("     " + line.strip()[:200])
    return p.returncode == 0


def ensure_helpers():
    """把仓库里的辅助脚本投放到微信实例容器（CTMP）。

    每次跑都同步一次，这样改了代码/换了版本立刻生效，不用手工 docker cp。
    需要：wxfind.py（发现 appId）、wxcdp.py（读 appId）、pkgprobe.py（判包）、
    wxclean.py（清残留窗口）、reopen_miniapp.py（开小程序；来自 WXSIGN_MINIAPP_PY）。
    """
    if not INSTANCE:
        return False
    # ⚠️ 2026-09-27 改为**按目录全投**（与 run_all.sh 的 `deploy_scripts` 用同一条规则），
    #    不再手工维护文件名清单。原来清单有两份（这里 + run_all.sh），已经漂移过一次：
    #    wxqm_auth.py / wxoppo*.py 只加在了一份里 → 「跑批时能用、单跑时报
    #    ModuleNotFoundError」。规则统一后，以后加脚本不用改任何地方。
    #    排除 wxsign.py 自己：它是**宿主机**上的引擎入口，容器里不跑，
    #    而且它要读 brands.json / brands/*.env，容器里没有这些文件。
    names = sorted(n for n in os.listdir(HERE)
                   if n.endswith(".py") and n != "wxsign.py")
    srcs = [(os.path.join(HERE, n), cpath(CTMP, n)) for n in names]
    extra = os.environ.get("WXSIGN_MINIAPP_PY", "")
    if extra and os.path.exists(extra):
        srcs.append((extra, cpath(CTMP, "wxopen.py")))    # 可选：用外部版本覆盖
    ok = True
    for s, d in srcs:
        if not os.path.exists(s):
            log("  [helpers] 缺 %s" % s)
            ok = False
            continue
        try:
            p = subprocess.run(["docker", "cp", s, "%s:%s" % (INSTANCE, d)],
                               capture_output=True, text=True, timeout=120)
            if p.returncode != 0:
                log("  [helpers] 投放 %s 失败：%s" % (s, (p.stderr or "").strip()[:120]))
                ok = False
        except Exception as e:
            log("  [helpers] 投放 %s 异常：%s" % (s, e))
            ok = False
    return ok


def discover_appid(kw, max_cards=6):
    """按品牌关键词自动发现 appId（开面板 → 搜索 → 逐张卡片点开 → 读 appId → 判包）。"""
    log("\n===== 发现 appId：搜索「%s」=====" % kw)
    if not INSTANCE:
        log("[!] 需要 WOC_INSTANCE（要操作微信界面）")
        return None
    ensure_helpers()
    args = ["docker", "exec", "-e", "DISPLAY=:1", "-e", "PYTHONUNBUFFERED=1", INSTANCE, CPY,
            cpath(CTMP, "wxfind.py"), kw, str(max_cards)]
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=1800)
    except Exception as e:
        log("[!] 执行异常：%s" % e)
        return None
    out = (p.stdout or "") + (p.stderr or "")
    data = None
    # 容器的全部输出都要透出来 —— 只挑 [find] 前缀会把真正的报错（[reopen] 那批）吞掉，踩过两次
    for line in out.splitlines():
        if line.startswith("FIND_JSON="):
            try:
                data = json.loads(line[len("FIND_JSON="):])
            except json.JSONDecodeError:
                pass
        elif line.strip():
            log(line)
    if not data:
        log("[!] 没拿到结果（rc=%s）" % p.returncode)
        return None

    rows = data.get("found", [])
    log("\n发现 %d 个号：" % len(rows))
    log("  %-4s %-24s %-14s %-8s %s" % ("#", "小程序名", "appId", "供应商", "包内特征"))
    for i, f in enumerate(rows, 1):
        sup = "吾享" if f.get("wuuxiang") else "其他"
        sign = ("有签到接口" if f.get("sign_api") else
                "有签到页" if f.get("sign_page") else
                "仅活动壳" if f.get("lot_api") else "无")
        log("  %-4s %-24s %-14s %-8s %s"
            % (i, f.get("name", "?"), f.get("appid", "?"), sup, sign))
    log("  （包内特征只是**初筛**，判断能不能签到要跑 --probe）")

    # 直接给出可以粘贴进 brands.json 的条目
    best = rows[0] if rows else None
    if best:
        log("\n最像签到载体的那个（按名字规律 + 包内特征排序）（复制进 brands.json 的 brands 数组）：")
        log(json.dumps({
            "slug": kw.replace(" ", ""),
            "name": kw,
            "appid": best.get("appid", ""),
            "keyword": kw,
            "miniapp": best.get("name", ""),
            "type": "sign",
            "enabled": True,
            "verified": False,
        }, ensure_ascii=False, indent=2))
    return data


# ───────────────────────── 业务动作 ─────────────────────────

def member_info(env, game_id="", third_shop=""):
    """查会员信息；顺便判定「是不是该品牌会员」。"""
    data = {"gameId": game_id, "gameType": 2, "thirdShopId": third_shop}
    r = api_post(env, "/api/member/single", data)
    return r, content_of(r)


def lot_list(env):
    """该租户的活动列表 —— 首页那些卡片就是它。签到/大转盘都在里面。"""
    r = api_post(env, "/api/game/lot/list", {})
    return r, content_of(r)


def register_member(brand, env):
    """账号还不是该品牌会员时自动注册（照抄小程序页面源码 registerVip 的入参）。

        POST /api/member/register
        data = {mobile, gameId, thirdShopId, byInviteCode}

    mobile 是**明文手机号** —— 加密串只有在走微信弹窗授权时才需要。
    `/api/member/register` 在**每个吾享游戏型包里都有**（实测 6/6），所以这条能通用。

    手机号是**可选的**，不是必须的 —— 这一点之前写错过：
      · 默认（不配手机号）走 **UI 授权**：驱动微信界面点「签到/参与入口 → 隐私协议 →
        授权 → 微信手机号授权弹窗 → 允许」，手机号由微信从你自己账号里给，全自动。
      · 配了手机号（`WX_REGISTER_PHONE` / `WXSIGN_REGISTER_PHONE`）才走 API 直连（零 UI，更快）。
      · 该账号绑了多个号码时，UI 路要用 `WX_REGISTER_PHONE_INDEX`（或手机号 + tesseract OCR）挑一行。
    同一个人可以在多个品牌各注册一次会员，互不影响。
    """
    mode = (env.get("WX_REGISTER_MODE") or os.environ.get("WXSIGN_REGISTER_MODE") or "auto").lower()
    phone = (env.get("WX_REGISTER_PHONE") or os.environ.get("WXSIGN_REGISTER_PHONE") or "").strip()
    if mode == "auto":
        mode = "api" if phone else "ui"
    log("  [register] 还不是会员 → 方式=%s 手机号=%s"
        % (mode, "已设（走 API 直连）" if phone else "(未设，走微信授权弹窗)"))

    if mode == "api":
        if not re.fullmatch(r"1\d{10}", phone):
            return False, "走 API 注册需要 11 位手机号（WX_REGISTER_PHONE 或 WXSIGN_REGISTER_PHONE）"
        r = api_post(env, "/api/member/register", {
            "mobile": phone,
            "gameId": env.get("WX_GAMEID", ""),
            "thirdShopId": env.get("WX_THIRDSHOPID", ""),
            "byInviteCode": "",
        })
        c = str(r.get("code"))
        log("  [register] /api/member/register → code=%s msg=%s" % (c, r.get("msg")))
        if c in ("200", "0"):
            return True, "API 注册成功"
        return False, "API 注册失败：%s（可改 WX_REGISTER_MODE=ui 走微信弹窗）" % r.get("msg")

    # UI 路径：驱动微信界面把授权走完（脚本内置为 wxreg.py，由 ensure_helpers 投放）。
    # 全自动 —— 手机号由微信从你自己账号里给，不需要你提供。
    if not INSTANCE:
        return False, "UI 注册需要 WOC_INSTANCE（要操作微信界面）"
    if not os.path.exists(os.path.join(HERE, "wxreg.py")):
        return False, "缺 wxreg.py（UI 注册脚本）"
    ensure_helpers()
    # 截图**按品牌分目录**：共用一个目录的话，下一个品牌会把上一个的覆盖掉，
    # 事后想回看「当时卡在哪一屏」就没了（踩过）。
    envs = ["-e", "DISPLAY=:1", "-e", "SHOT_DIR=" + cpath(CTMP, "shots", brand["slug"])]
    if env.get("WX_REGISTER_PHONE_INDEX"):
        envs += ["-e", "WXSIGN_REGISTER_PHONE_INDEX=" + env["WX_REGISTER_PHONE_INDEX"]]
    if phone:
        envs += ["-e", "WXSIGN_REGISTER_PHONE=" + phone]
    if os.environ.get("WXSIGN_REG_CLICK"):
        envs += ["-e", "WXSIGN_REG_CLICK=" + os.environ["WXSIGN_REG_CLICK"]]
    if os.environ.get("WXSIGN_REG_SCROLL"):
        envs += ["-e", "WXSIGN_REG_SCROLL=" + os.environ["WXSIGN_REG_SCROLL"]]
    try:
        p = subprocess.run(["docker", "exec"] + envs + [INSTANCE, CPY, cpath(CTMP, "wxreg.py")],
                           capture_output=True, text=True, timeout=900)
    except Exception as e:
        return False, "UI 注册异常：%s" % e
    for line in ((p.stdout or "") + (p.stderr or "")).splitlines()[-25:]:
        log("     " + line[:200])
    if p.returncode == 4:
        return False, "UI 注册没跑：当前不在小程序页面（要先把目标小程序开在签到/活动页）"
    if p.returncode != 0:
        return False, ("UI 注册没走完（rc=%s）—— 看上面的 [ui] 日志与 %s/shots/reg_*.png。"
                       "入口坐标可能需要按品牌调（WXSIGN_REG_CLICK=x,y，比例或像素）"
                       % (p.returncode, CTMP))
    return True, "UI 注册流程已跑"


def pick_activity(items, want=("签到", "打卡", "参与", "领取", "抽奖", "sign")):
    """从活动列表里挑出「签到」那个活动。返回 (activity, 命中理由)。

    三级判据（越靠前越可信）—— 反馈：「别人也不一定叫『立即签到』」，所以不能只看文案：
      ① 名字命中签到类**词根**（品牌文案有差异，只匹配词根）
      ② 活动 **type** 命中打卡签到类型（来菜实测 type="2"，名字也叫「每日签到」）
      ③ 列表里**只有一个**活动 → 直接用（很多租户就配了一个）
    """
    if isinstance(items, dict):
        for k in ("list", "records", "rows", "content", "data"):
            if isinstance(items.get(k), list):
                items = items[k]
                break
    if not isinstance(items, list):
        return None, "活动列表结构未识别"
    acts = [it for it in items if isinstance(it, dict)]
    for it in acts:
        blob = " ".join(str(it.get(k, "")) for k in
                        ("name", "title", "activityName", "remark"))
        for w in want:
            if w in blob:
                return it, "命中「%s」" % w
    for it in acts:
        t = str(it.get("type") or it.get("gameType") or "")
        if t == "2":
            return it, "活动类型 type=2（打卡签到）"
    if len(acts) == 1:
        return acts[0], "列表只有一个活动，直接用它"
    return None, "列表里挑不出签到活动（共 %d 个）" % len(acts)


def discover_lot_gameid(env):
    """lot 型：从活动列表里认出签到活动的 id —— 这个能自动，不用进页面抄。
    返回 (gameId, 说明)。只有一个活动时直接用那一个。"""
    r, items = lot_list(env)
    if str(r.get("code")) != CODE_OK:
        return "", "活动列表接口 code=%s msg=%s" % (r.get("code"), r.get("msg"))
    act, why = pick_activity(items)
    if not act:
        return "", why
    for k in ("gameId", "id"):
        if act.get(k):
            return str(act[k]), "%s「%s」" % (why, act.get("name") or act.get("title") or "")
    return "", "活动项里没有 id 字段"


# ───────────────────── 易东（eingdong）后端 ─────────────────────
# 仓库里的第二个后端。吾享那套（crm7game-api + mpId/openId + 签名）在这里完全不适用：
# 易东是「明文 PHP 路由 + cookie sessionKey」，结构上比吾享还简单。实测记录见 survey/YD_API.md。
#   取身份（小程序逻辑层，wxyd.py）→ POST /api/login 换 sessionKey
#   → cookie: sessionKey=<sk> → POST /signin/get_info 查状态 → /signin/check_in_1 签到
YD_BASE = "https://zhyx.eingdong.com/api/index.php"
_YD_HOST = YD_BASE.split("//", 1)[1].split("/", 1)[0]
_YD_PREFIX = "/" + YD_BASE.split("//", 1)[1].split("/", 1)[1].strip("/")
_YD_CONN = [None]


def _yd_post_once(path, data, cookie=""):
    """单次 POST。也复用连接 —— 理由与 _api_post_once 完全相同（临时端口耗尽）。"""
    body = urllib.parse.urlencode(data).encode()
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if cookie:
        headers["cookie"] = cookie
    try:
        conn = _YD_CONN[0]
        if conn is None:
            conn = http.client.HTTPSConnection(_YD_HOST, timeout=20, context=_SSL)
            _YD_CONN[0] = conn
        conn.request("POST", _YD_PREFIX + path, body=body, headers=headers)
        r = conn.getresponse()
        raw = r.read().decode(errors="replace")
        if r.status != 200:
            return {"status": str(r.status), "msg": "HTTP %s: %s" % (r.status, raw[:200])}
        return json.loads(raw)
    except Exception as e:
        _YD_CONN[0] = None
        return {"_transport": 1, "msg": "传输失败：%s" % e}


def yd_post(path, data, cookie="", retries=3):
    """带重试的 POST。判据用 `_transport`（易东的业务字段是 status/msg，不是 code）。"""
    r = None
    for attempt in range(retries + 1):
        r = _yd_post_once(path, data, cookie)
        if not r.get("_transport"):
            return r
        if attempt < retries:
            wait = 2.0 * (attempt + 1)
            log("  [net] %s 第 %d 次失败（%s），%.1fs 后重试"
                % (path, attempt + 1, str(r.get("msg"))[:60], wait))
            time.sleep(wait)
    return r


def yd_ident(brand, wait=15):
    """在小程序逻辑层取 (code, storeid)；失败返回 (None, None)。

    前提：目标小程序**正开着**（前一步 ensure_miniapp 刚开过）且 hook 通。
    code 只用一次、约 5 分钟内有效，所以拿到就立刻用，不要缓存。
    """
    if not INSTANCE:
        log("  [yd] 未设 WOC_INSTANCE → 取不到 code")
        return None, None
    ensure_helpers()
    try:
        p = subprocess.run(["docker", "exec", "-e", "DISPLAY=:1", "-e", "PYTHONUNBUFFERED=1", INSTANCE, CPY,
                            cpath(CTMP, "wxyd.py"), brand.get("appid", ""), str(wait)],
                           capture_output=True, text=True, timeout=180)
    except Exception as e:
        log("  [yd] 执行异常：%s" % e)
        return None, None
    out = (p.stdout or "") + (p.stderr or "")
    got = None
    for line in out.splitlines():
        if line.startswith("YD_JSON="):
            try:
                got = json.loads(line[len("YD_JSON="):])
            except ValueError:
                pass
        elif line.strip():
            log("     " + line.strip()[:180])
    if not got:
        log("  [yd] 没拿到身份（wxyd.py rc=%s）" % p.returncode)
        return None, None
    return got.get("code"), str(got.get("storeid") or "")


def do_sign_yd(brand, env):
    """易东后端签到。返回 (是否成功, 业务码, 说明)。"""
    storeid = env.get("YD_STOREID", "")
    code, got_store = yd_ident(brand)
    if not code:
        return False, "NOCODE", "拿不到 wx.login 的 code（小程序开着吗？hook 通吗？）"
    if got_store:
        storeid = got_store
    if not storeid:
        return False, "NOSTORE", "拿不到门店 storeid（小程序 ext 配置里没有？）"

    r = yd_post("/api/login", {"code": code, "storeid": storeid,
                               "ext_storeid": storeid,
                               "v": env.get("YD_VERSION", "1"), "parent_id": "0"})
    if str(r.get("status")) != "1":
        return False, "LOGIN", "换 sessionKey 失败：%s" % str(r.get("msg"))[:160]
    sk = r.get("sessionId", "")
    if not sk:
        return False, "LOGIN", "登录应答里没有 sessionId"
    log("  [yd] 已登录：门店「%s」 openId=%s"
        % (r.get("store_name", storeid), r.get("openId", "")))
    if env.get("YD_SESSIONKEY") != sk:          # 落盘只是省一次 login，不是必需
        env["YD_SESSIONKEY"] = sk
        env["YD_STOREID"] = storeid
        save_env(brand["slug"], env)

    cookie = "sessionKey=" + sk
    info = yd_post("/signin/get_info", {"storeid": storeid}, cookie=cookie)
    if str(info.get("status")) != "1":
        return False, "INFO", "查签到状态失败：%s" % str(info.get("msg"))[:160]
    d = info.get("info") or {}
    if str(d.get("signed_today", "0")) == "1":
        return True, "415", "今天已经签到过了（连续 %s 天）" % d.get("keep_days", "?")

    r = yd_post("/signin/check_in_1", {"storeid": storeid}, cookie=cookie)
    if str(r.get("status")) == "1":
        rw = r.get("reward") or {}
        return True, "200", "签到成功：+%s，连续 %s 天" % (rw.get("reward_amount"), rw.get("keep_days"))
    return False, str(r.get("status")), str(r.get("msg"))[:160]


# ───────────────────── 微租林（weizulin）后端 ─────────────────────
# 第三个后端。base 与 x-appid **都是包内明文**（app.js 顶层一个配置对象）：
#   {APPID: "app-cf9187c281ff", BASE_URL: "https://saas.funjs.top/api", VERSION: "1.0.0"}
# 链路（每一步都实测过，见 survey/WZL_API.md）：
#   逻辑层取 wx.login 的 code（wxcode.py）
#   → POST {base}/open/auth/mp/silent-login  (JSON {code}，header x-appid)  → data.token
#   → 之后带 Authorization: Bearer <token> + x-appid + x-client-source: applet
#   → GET  /open/check-in/status  查状态（data.todayChecked）
#   → POST /open/check-in         签到（data.checkedIn / streakDays / rewardCount）
# ⚠️ x-appid 是**微租林平台内**的应用 ID（每个小程序一个），不是微信 appId
#    → 放 brands.json 的 wzl_appid（从包内 app.js 那个配置对象里抄）。
WZL_BASE = "https://saas.funjs.top/api"
_WZL_HOST = WZL_BASE.split("//", 1)[1].split("/", 1)[0]
_WZL_PREFIX = "/" + WZL_BASE.split("//", 1)[1].split("/", 1)[1].strip("/")
_WZL_CONN = [None]


def _wzl_once(method, path, data=None, token="", appid=""):
    """单次请求。也复用连接 —— 理由与 _api_post_once 完全相同（临时端口耗尽）。"""
    headers = {"Content-Type": "application/json", "x-client-source": "applet"}
    if appid:
        headers["x-appid"] = appid
    if token:
        headers["Authorization"] = "Bearer " + token
    body = json.dumps(data).encode() if data is not None else None
    try:
        conn = _WZL_CONN[0]
        if conn is None:
            conn = http.client.HTTPSConnection(_WZL_HOST, timeout=20, context=_SSL)
            _WZL_CONN[0] = conn
        conn.request(method, _WZL_PREFIX + path, body=body, headers=headers)
        r = conn.getresponse()
        raw = r.read().decode(errors="replace")
        # ⚠️ 别按 HTTP 状态码判成败：401 也带着 JSON（`{"code":401,"message":"未登录…"}`），
        #    业务码在 body 的 `code` 里。能解析就当业务返回，解析不了才算传输失败。
        try:
            return json.loads(raw)
        except ValueError:
            return {"_transport": 1, "msg": "HTTP %s 且非 JSON：%s" % (r.status, raw[:200])}
    except Exception as e:
        _WZL_CONN[0] = None
        return {"_transport": 1, "msg": "传输失败：%s" % e}


def wzl_request(method, path, data=None, token="", appid="", retries=3):
    """带重试的请求。判据用 `_transport`（微租林的业务字段是 code/message）。"""
    r = None
    for attempt in range(retries + 1):
        r = _wzl_once(method, path, data, token, appid)
        if not r.get("_transport"):
            return r
        if attempt < retries:
            wait = 2.0 * (attempt + 1)
            log("  [net] %s 第 %d 次失败（%s），%.1fs 后重试"
                % (path, attempt + 1, str(r.get("msg"))[:60], wait))
            time.sleep(wait)
    return r


def wzl_ident(brand, wait=15):
    """在小程序逻辑层取 wx.login 的 code；失败返回 None。

    code 一次一用、约 5 分钟失效 —— 拿到就立刻用，不要缓存。
    前提：目标小程序**正开着**（前一步 ensure_miniapp 刚开过）且 hook 通。
    """
    if not INSTANCE:
        log("  [wzl] 未设 WOC_INSTANCE → 取不到 code")
        return None
    ensure_helpers()
    try:
        p = subprocess.run(["docker", "exec", "-e", "DISPLAY=:1", "-e", "PYTHONUNBUFFERED=1", INSTANCE, CPY,
                            cpath(CTMP, "wxcode.py"), brand.get("appid", ""), str(wait)],
                           capture_output=True, text=True, timeout=180)
    except Exception as e:
        log("  [wzl] 执行异常：%s" % e)
        return None
    out = (p.stdout or "") + (p.stderr or "")
    got = None
    for line in out.splitlines():
        if line.startswith("WZ_JSON="):
            try:
                got = json.loads(line[len("WZ_JSON="):])
            except ValueError:
                pass
        elif line.strip():
            log("     " + line.strip()[:180])
    if not got:
        log("  [wzl] 没拿到 code（wxcode.py rc=%s）" % p.returncode)
        return None
    return got.get("code") or None


def do_sign_wzl(brand, env):
    """微租林后端签到。返回 (是否成功, 业务码, 说明)。"""
    appid = env.get("WZL_APPID") or brand.get("wzl_appid", "")
    if not appid:
        return False, "NOAPP", "缺 wzl_appid（微租林平台内的应用 ID，见包内 app.js 的配置对象）"

    def _say(r):
        return "%s（code=%s）" % (str(r.get("message", ""))[:120], r.get("code"))

    # ① 先试缓存 token；被拒（401）就丢掉重登 —— token 是 JWT，实测有效期约 7 天。
    token = env.get("WZL_TOKEN", "")
    st = None
    if token:
        st = wzl_request("GET", "/open/check-in/status", token=token, appid=appid)
        if str(st.get("code")) == "401" or st.get("_transport"):
            token = ""

    # ② 重新登录：code → token
    if not token:
        code = wzl_ident(brand)
        if not code:
            return False, "NOCODE", "拿不到 wx.login 的 code（小程序开着吗？hook 通吗？）"
        r = wzl_request("POST", "/open/auth/mp/silent-login", {"code": code}, appid=appid)
        d = r.get("data") if isinstance(r.get("data"), dict) else {}
        token = d.get("token", "")
        if not token:
            return False, "LOGIN", "静默登录失败：%s" % _say(r)
        log("  [wzl] 已登录：openId=%s 用户=%s"
            % (d.get("openId", ""), (d.get("user") or {}).get("nickname", "")))
        env["WZL_TOKEN"] = token
        env["WZL_APPID"] = appid
        save_env(brand["slug"], env)
        st = wzl_request("GET", "/open/check-in/status", token=token, appid=appid)

    # ③ 状态
    if str(st.get("code")) != "0":
        return False, "INFO", "查签到状态失败：%s" % _say(st)
    d = st.get("data") or {}
    if not d.get("enabled"):
        return False, "NOACT", "该应用未开启签到"
    if d.get("todayChecked"):
        return True, "415", "今天已经签到过了（连续 %s 天）" % d.get("streakDays", "?")

    # ④ 签到
    r = wzl_request("POST", "/open/check-in", {}, token=token, appid=appid)
    if str(r.get("code")) != "0":
        m = str(r.get("message", ""))
        if "已" in m or "重复" in m:          # 幂等：并发/重跑时它可能已经签过了
            return True, "415", m[:120]
        return False, str(r.get("code")), _say(r)
    d = r.get("data") or {}
    return True, "200", "签到成功：+%s 次%s，连续 %s 天（余额 %s）" % (
        d.get("rewardCount", "?"),
        "（连签奖励）" if d.get("isStreakBonus") else "",
        d.get("streakDays", "?"), d.get("bonusCountBalance", "?"))


# ── 企迈（qmai）后端 ──────────────────────────────────────────────────
# 商户号 storeId 与 Qm-User-Token 都是**服务端下发的 config**，存在小程序逻辑层
# storage 的 `loginData` 里（由 wxqm.py 读出来）。接口是平台级 REST：
#     POST https://webapi.qmai.cn/web/<biz>/integral/sign/detail | rule | signIn
# 认证只要两个头 —— `store-id: <商户号>` + `Qm-User-Token: <会话>`，**没有签名**。
#
# ⚠️ 两个容易栽的坑（都是实测踩出来的）：
#   1) **路径必须带 `/web` 前缀**。少这一节，阿里云 WAF 会拿「不存在的路由」
#      甩回一个 110310 字节的 JS 挑战页 —— 极易被误读成「被墙了」，
#      实际只是自己拼错了 URL（Punish-Loc: keepper 是这么来的）。
#   2) **`<biz>` 按业务线不同**：餐饮大盘是 `catering`，呷哺系是 `mealmate-apiserver`。
#      实测两条前缀**都能通**，所以缺省用 `catering`，要覆盖就在 brands.json 写 `qm_biz`。
QM_BASE = "https://webapi.qmai.cn"
_QM_HOST = QM_BASE.split("//", 1)[1]
_QM_CONN = [None]
QM_BIZ = {"catering": "catering", "mealmate": "mealmate-apiserver"}


def _qm_once(path, data, token="", store_id="", biz="catering"):
    """单次请求。复用连接 —— 理由与 _api_post_once / _wzl_once 相同（临时端口耗尽）。"""
    headers = {
        "Content-Type": "application/json",
        "Accept": "v=1.0",
        "Qm-From": "wechat",
        "Qm-From-Type": biz,
        "store-id": str(store_id),
        "scene": "1101",
    }
    if token:
        headers["Qm-User-Token"] = token
    body = json.dumps(data).encode() if data is not None else b"{}"
    try:
        conn = _QM_CONN[0]
        if conn is None:
            conn = http.client.HTTPSConnection(_QM_HOST, timeout=20, context=_SSL)
            _QM_CONN[0] = conn
        conn.request("POST", path, body=body, headers=headers)
        r = conn.getresponse()
        raw = r.read().decode(errors="replace")
        # 别按 HTTP 状态码判成败：业务码全在 body 的 `code` 里
        # （10008 未登录 / 9001 登录超时 / 400042 商户未开启 / 20013 活动ID为空）。
        try:
            return json.loads(raw)
        except ValueError:
            return {"_transport": 1, "msg": "HTTP %s 且非 JSON（前 160 字：%s）"
                                            % (r.status, raw[:160])}
    except Exception as e:
        _QM_CONN[0] = None
        return {"_transport": 1, "msg": "传输失败：%s" % e}


def qm_request(path, data, token="", store_id="", biz="catering", retries=3):
    """带重试。判据用 `_transport`（企迈的业务字段是 status/code/message）。"""
    r = None
    for attempt in range(retries + 1):
        r = _qm_once(path, data, token, store_id, biz)
        if not r.get("_transport"):
            return r
        if attempt < retries:
            wait = 2.0 * (attempt + 1)
            log("  [net] %s 第 %d 次失败（%s），%.1fs 后重试"
                % (path, attempt + 1, str(r.get("msg"))[:60], wait))
            time.sleep(wait)
    return r


def qm_ident(brand, wait=12):
    """在小程序逻辑层读企迈的登录态（Qm-User-Token + 商户 storeId）。

    与易东（wxyd.py 要 wx.login 的 code）不同：**企迈只要现成的会话**。
    小程序打开时会自己完成一次静默登录并把结果写进 storage 的 `loginData`，
    我们直接读即可 —— 所以这一步是同步的，没有异步等待。
    """
    if not INSTANCE:
        log("  [qm] 未设 WOC_INSTANCE → 取不到登录态")
        return None
    ensure_helpers()
    try:
        p = subprocess.run(["docker", "exec", "-e", "DISPLAY=:1", "-e", "PYTHONUNBUFFERED=1", INSTANCE, CPY,
                            cpath(CTMP, "wxqm.py"), brand.get("appid", ""), str(wait)],
                           capture_output=True, text=True, timeout=180)
    except Exception as e:
        log("  [qm] 执行异常：%s" % e)
        return None
    out = (p.stdout or "") + (p.stderr or "")
    got = None
    for line in out.splitlines():
        if line.startswith("QM_JSON="):
            try:
                got = json.loads(line[len("QM_JSON="):])
            except ValueError:
                pass
        elif line.strip():
            log("     " + line.strip()[:180])
    if not got:
        log("  [qm] 没读到登录态（wxqm.py rc=%s）" % p.returncode)
        return None
    return got


def do_sign_qm(brand, env):
    """企迈后端签到。返回 (是否成功, 业务码, 说明)。

    ✅ 2026-09-26 **真机跑通**（呷哺呷哺：界面弹「签到成功！恭喜获得 1 哺币」，
       积分 0→1、连签 0→1 天），随后三个接口用 curl 复核全部返回真实数据。
    ✅ 2026-09-26 **第二个品牌跑通**（李先生牛肉面大王：`takePartInSign` 回 `status=true`，
       界面变「已签到」、连签 0→1 天、拿到「满35减2元券」）。

    链路（URL 里**不带业务线前缀**，业务线只在 `Qm-From-Type` 头里）：
        POST /web/cmk-center/sign/userSignStatistics  先看今天签没签
        POST /web/cmk-center/sign/takePartInSign      签到
    请求体两个字段：`activityId`（商户级活动 ID）+ `storeId`。
    ⚠️ 抓包看到真实请求体里带 `appid`、不带 `storeId`，于是我以为 body 该用 appid；
       但**实测两者都行** —— `userSignStatistics` 对 `{activityId}` / `{activityId,storeId}` /
       `{activityId,appid}` / 全带 **四种都回 code=0**。身份其实来自
       `Qm-User-Token` + `store-id` **请求头**，body 里那点差异它不挑。
       所以沿用 `{activityId, storeId}` 即可，别为这个改。
    响应字段（⚠️ 曾据此写错判据）：`signStatus`（**1=今日已签 / 2=未签**）+ `signDays`（连签天数）；
       奖励在 `rewardDetailList[].rewardName`。

    ⚠️ 四个坑 —— 每一个都让我错判过一轮，记下来：
      1. **`cmk-center/sign/*` 才是「签到有礼」活动**。包里另有一套 `integral/sign/*`
         （积分商城那套），它对同一商户的 `detail` 会回 `400042 商家未开启此功能` ——
         **那是个误导性的错误码，别拿它当判据**。我先后用错页面（`subpackages/sign-in`）
         和错接口（`integral/sign`），连着下了三次「这商户没开活动」的错误结论。
      2. **必须已登录（绑手机号）**。未登录时签到页的 `onClickCheckin()` 会先
         `popAuthorization()` 弹授权、**根本不发请求**；接口层回 `100005 用户未登录`。
         ⚠️ 但绑定是**一次性**的 —— 绑在服务端 openid 上、**永久有效**，绑完每次静默
         登录拿到的 token 就已经是「已绑」状态。所以日常签到是全自动的（实测：呷哺连着几天
         自动跑通），只有**接入该品牌的第一次**要过一遍授权。
         那一次也已经自动了：`wxqm_auth.py`（① CDP 调 `popAuthorization()` 弹授权层，
         ②③④ 勾选 / 点「手机号一键登录」/ 点微信原生「允许」都走系统级点击 ——
         `getPhoneNumber` 是 `open-type` 按钮，微信按真实手势处理）。
      3. **`activityId` 是商户级活动的固定 ID**（随签到入口由服务端下发，包里没有），
         写进 brands.json 的 `qm_activity`。取法：进「我的」→「每日签到」，
         读签到页 data 的 `activityId`，或抓 `userSignStatistics` 的请求体。
         ⚠️ 2026-09-26 补充：企迈「我的」页那四个入口**是图片热区**
         （`WX-QM-IMAGE-HOT`，文字画在图里、`innerText` 读不到），
         所以「按文案找入口」这条路走不通 —— 正确做法是读热区组件 data 的
         `hotList[].linkType`（签到 = `newCheckIn`），从它的 `link` 里取 `activityId`。
      4. **未绑手机号时签到回的是 `100027 当前渠道不能参与活动`**，不是渠道参数错。
         这个错误码读起来像「Qm-From-Type 配错了」，**其实真因是「你不是本渠道有效会员」**
         （服务端没绑手机号）。我在渠道路径上白绕了一圈，才回到「先把手机号绑上」。
    """
    activity = env.get("QM_ACTIVITY") or brand.get("qm_activity") or ""
    if not activity:
        return False, "NOACTID", "缺 qm_activity（商户级签到活动 ID，取法见本函数 docstring）"

    ident = qm_ident(brand)
    if not ident:
        return False, "NOIDENT", "拿不到企迈登录态（小程序开着吗？hook 通吗？）"
    token = ident.get("token") or ""
    store_id = str(ident.get("storeId") or "")
    if not (token and store_id):
        return False, "NOIDENT", "登录态不完整：token=%s storeId=%s" % (bool(token), store_id or "(空)")
    # 业务线优先用**抓包读到的** `Qm-From-Type`（最准，不用配）；其次 brands.json 的 qm_biz。
    biz_key = ident.get("biz") or brand.get("qm_biz") or "catering"
    biz = QM_BIZ.get(biz_key, biz_key)
    # ⚠️ `cmk-center` 是**不带业务线前缀**的一级路径 —— 实测带 `/mealmate-apiserver`
    #    会回 `43004 http状态码异常`（"路径不存在"的伪装）。业务线只体现在
    #    `Qm-From-Type` 请求头里，不进 URL。
    pre = "/web"
    body = {"activityId": str(activity), "storeId": store_id}

    def _say(r):
        return "%s（code=%s）" % (str(r.get("message", ""))[:120], r.get("code"))

    # ① 查统计：既判断今天签没签，也顺手验证会话是否有效
    st = qm_request(pre + "/cmk-center/sign/userSignStatistics", body, token, store_id, biz)
    if st.get("_transport"):
        return False, "NETFAIL", "网络失败：%s" % str(st.get("msg"))[:120]
    sc = str(st.get("code"))
    if sc in ("100005", "10008", "9001"):
        return False, "NOTOKEN", "企迈会话失效/未登录（%s）→ 需在小程序里过一次手机号授权" % sc

    sd = st.get("data") or {}
    # ⚠️ 2026-09-26 实测更正：`userSignStatistics` 的返回字段**不是** todaySign/signToday，
    #    而是 `signStatus`（**1 = 今天已签，2 = 未签**）+ `signDays`（连签天数）。
    #    旧版判的是 todaySign/signToday/todaySigned，**一个都不存在** → 恒判「未签到」→
    #    每天都会多打一次 takePartInSign。实测李先生那天：签到前 signStatus=2 signDays=0，
    #    签到后 signStatus=1 signDays=1。
    if str(sd.get("signStatus")) == "1":
        return True, "415", "今天已经签到过了（连签 %s 天）" % (sd.get("signDays") or "?")
    # 旧字段名留作兜底（万一别的商户活动类型用另一套）
    if sd.get("todaySign") or sd.get("signToday") or sd.get("todaySigned"):
        return True, "415", "今天已经签到过了（连签 %s 天）" % (
            sd.get("continueSignDays") or sd.get("continuousDays") or "?")

    # ② 签到
    r = qm_request(pre + "/cmk-center/sign/takePartInSign", body, token, store_id, biz)
    if r.get("_transport"):
        return False, "NETFAIL", "网络失败：%s" % str(r.get("msg"))[:120]
    rc = str(r.get("code"))
    if rc in ("100005", "10008", "9001"):
        return False, "NOTOKEN", "企迈会话失效/未登录（%s）" % rc
    msg = str(r.get("message", ""))
    if r.get("status"):
        dd = r.get("data") or {}
        # 奖励在 `rewardDetailList`（列表，每项有 `rewardName`），不是 `rewardName` 字段
        got = dd.get("rewardName") or dd.get("points") or dd.get("rewardValue")
        if not got:
            rl = dd.get("rewardDetailList") or []
            names = [x.get("rewardName") for x in rl if isinstance(x, dict) and x.get("rewardName")]
            got = "、".join(names)
        return True, "200", "签到成功%s" % (("：%s" % got) if got else "")
    if "已签到" in msg or "已经签到" in msg:
        return True, "415", msg[:120]
    return False, rc, _say(r)


# ── OPPO 商城（oppo）后端 ─────────────────────────────────────────────
# 会话是 `NEWOPPOSID` + `openid` 两个请求头（s_channel=program_wx）。这两个值由小程序
# 自己拿 wx.login 的 code 去 OPPO 服务端换 —— 我们拿不到也不需要 code，只读它发业务
# 请求时带出来的头（wxoppo.py，容器内跑）。这正是 2026-07 那版 get_token.py 验证过的路。
# 接口（2026-07 已验证）：
#     GET  /users/web/member/info                              验会话
#     GET  /cn/oapi/marketing/cumulativeSignIn/getSignInDetail 查签到档期/进度
#     POST /cn/oapi/marketing/cumulativeSignIn/signIn          签到（body {activityId}）
OPPO_BASE = "https://msec.opposhop.cn"
_OPPO_HOST = OPPO_BASE.split("//", 1)[1]
_OPPO_CONN = [None]
# OPPO 网关认的固定头。s_version 随小程序版本变、Referer 随活动页变，都允许 brands.json 覆盖。
OPPO_FIXED_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "s_channel": "program_wx",
    "source_type": "503",
    "Personalized": "1",
    "xweb_xhr": "1",
}


def _oppo_once(path, data=None, sid="", openid="", method="GET", extra=None):
    """单次请求。复用连接 —— 理由与 _api_post_once / _qm_once 相同（临时端口耗尽）。"""
    headers = dict(OPPO_FIXED_HEADERS)
    headers.update(extra or {})
    if sid:
        headers["NEWOPPOSID"] = sid
    if openid:
        headers["openid"] = openid
    body = json.dumps(data).encode() if data is not None else None
    try:
        conn = _OPPO_CONN[0]
        if conn is None:
            conn = http.client.HTTPSConnection(_OPPO_HOST, timeout=20, context=_SSL)
            _OPPO_CONN[0] = conn
        conn.request(method, path, body=body, headers=headers)
        r = conn.getresponse()
        raw = r.read().decode(errors="replace")
        # 业务码全在 body 的 code 里；被 WAF 拦时会回 HTML，按非 JSON 记传输失败
        try:
            return json.loads(raw)
        except ValueError:
            return {"_transport": 1, "msg": "HTTP %s 且非 JSON（前 160 字：%s）"
                                            % (r.status, raw[:160])}
    except Exception as e:
        _OPPO_CONN[0] = None
        return {"_transport": 1, "msg": "传输失败：%s" % e}


def oppo_request(path, data=None, sid="", openid="", method="GET", extra=None, retries=3):
    """带重试。判据用 `_transport`（OPPO 的业务字段是 code/message）。"""
    r = None
    for attempt in range(retries + 1):
        r = _oppo_once(path, data, sid, openid, method, extra)
        if not r.get("_transport"):
            return r
        if attempt < retries:
            wait = 2.0 * (attempt + 1)
            log("  [net] %s 第 %d 次失败（%s），%.1fs 后重试"
                % (path, attempt + 1, str(r.get("msg"))[:60], wait))
            time.sleep(wait)
    return r


def oppo_ident(brand, wait=12):
    """在小程序里读 OPPO 的会话（NEWOPPOSID + openid），见 wxoppo.py。"""
    if not INSTANCE:
        log("  [oppo] 未设 WOC_INSTANCE → 取不到会话")
        return None
    ensure_helpers()
    try:
        p = subprocess.run(["docker", "exec", "-e", "DISPLAY=:1", INSTANCE, CPY,
                            cpath(CTMP, "wxoppo.py"), brand.get("appid", ""), str(wait)],
                           capture_output=True, text=True, timeout=180)
    except Exception as e:
        log("  [oppo] 执行异常：%s" % e)
        return None
    out = (p.stdout or "") + (p.stderr or "")
    got = None
    for line in out.splitlines():
        if line.startswith("OPPO_JSON="):
            try:
                got = json.loads(line[len("OPPO_JSON="):])
            except ValueError:
                pass
        elif line.strip():
            log("     " + line.strip()[:180])
    if not got:
        log("  [oppo] 没读到会话（wxoppo.py rc=%s）" % p.returncode)
        return None
    return got


def do_sign_oppo(brand, env):
    """OPPO 商城后端签到。返回 (是否成功, 业务码, 说明)。

    链路：wxoppo.py 读 NEWOPPOSID+openid → 查签到档期 → POST signIn {activityId}。
    返回码：200 成功 / 5008 今日已签 / 5005 活动不存在（activityId 换档期了）。
    """
    activity = env.get("OPPO_ACTIVITY") or brand.get("oppo_activity") or ""
    if not activity:
        return False, "NOACTID", "缺 oppo_activity（OPPO 签到活动 ID，换档期时要更新）"
    extra = {
        "s_version": str(env.get("OPPO_SVERSION") or brand.get("oppo_sversion") or "80457"),
        "Referer": env.get("OPPO_REFERER") or brand.get("oppo_referer")
                   or "https://hd.opposhop.cn/bp/b371ce270f7509f0",
        "User-Agent": UA,
    }

    ident = oppo_ident(brand)
    if not ident or not ident.get("sid"):
        return False, "NOIDENT", "拿不到 OPPO 会话 NEWOPPOSID（小程序开着吗？hook 通吗？）"
    sid, openid = ident.get("sid", ""), ident.get("openid", "")
    # 小程序真实请求里除了 NEWOPPOSID/openid 还带 `constToken`（另一个值）。
    # 实测它不是必需的（只留 NEWOPPOSID+openid 也能通），但既然抓到就原样带上，少一个变量。
    if ident.get("const_token"):
        extra["constToken"] = ident["const_token"]

    # ① 「你是谁」——**只打印，不作门槛**。
    #    ⚠️⚠️ 2026-09-27 两处纠正：
    #    (a) 老版本拿这个接口的 code==200 当「会话有效」的硬门槛，而它现在**恒回 403**
    #        （接口级拦截/WAF，不是会话问题）→ 把明明有效的会话判成 NOTOKEN、
    #        在签到**之前**就中止了（实测踩到）。
    #    (b) 路径也写错了：真实路径是 `/users/web/member/infoDetail`（带 Detail），
    #        `/users/web/member/info` 不存在 → 403 有一部分就是这么来的。
    #    现在：换成正确路径、且**成功与否都不拦**，会话有效性交给签到接口自己回答。
    me = oppo_request("/users/web/member/infoDetail", sid=sid, openid=openid, extra=extra)
    if str(me.get("code")) == "200" and me.get("data"):
        d = me["data"] if isinstance(me["data"], dict) else {}
        log("  [member] 会话对应账号：%s" % (d.get("userName") or d.get("nickName") or d.get("phone") or "?"))
    else:
        log("  [member] member/infoDetail 回 code=%s（不拦，继续签到）" % me.get("code"))

    # ② 查档期/进度 —— **这一步是幂等判据的来源**（2026-09-27 新增）。
    #    ⚠️ 为什么不再"先试签一次、靠错误码认已签"：活动换档期后 signIn 会回
    #    `5007 活动已经结束`，与"今天签过了"**语义完全混淆**（实测踩到：拿 7 月的
    #    activityId 去签，服务端回 5007，被误当作失败）。而 `todaySignIn` 是服务端
    #    直接背书的「今天签过了」，判它干净、准，还省一次写请求。
    det = oppo_request("/cn/oapi/marketing/cumulativeSignIn/getSignInDetail?activityId=%s" % activity,
                       sid=sid, openid=openid, extra=extra)
    if str(det.get("code")) == "200" and isinstance(det.get("data"), dict):
        dd = det["data"]
        awards = dd.get("baseAwards") or []
        done = sum(1 for a in awards if isinstance(a, dict) and a.get("status") == 1)
        log("  [info] 签到档期 OK：本轮 %d/%d 天，今日已签=%s"
            % (done, len(awards), dd.get("todaySignIn")))
        if dd.get("todaySignIn") is True:
            return True, "415", "今日已签到（todaySignIn=true，本轮 %d/%d 天）" % (done, len(awards))
    else:
        log("  [info] 查档期回 code=%s msg=%s（继续尝试签到）"
            % (det.get("code"), str(det.get("message") or det.get("msg") or "")[:80]))

    # ③ 签到（5008/已签 视为幂等成功）
    r = oppo_request("/cn/oapi/marketing/cumulativeSignIn/signIn",
                     {"activityId": int(activity)}, sid=sid, openid=openid,
                     method="POST", extra=extra)
    if r.get("_transport"):
        return False, "NETFAIL", "网络失败：%s" % str(r.get("msg"))[:120]
    rc = str(r.get("code"))
    msg = str(r.get("message") or r.get("msg") or "")
    if rc == "200":
        return True, "200", "签到成功%s" % (("：" + json.dumps(r.get("data"), ensure_ascii=False)[:120])
                                          if r.get("data") else "")
    if rc == "5008" or "已签" in msg or "签过" in msg:
        return True, "415", msg[:120] or "今日已签到"
    if rc in ("5005", "5007"):
        # 5005 = 活动不存在；5007 = 活动已经结束（**换档期**的典型症状）
        return False, "NOACT", "活动不存在/已结束（oppo_activity 换档期了，要更新）：%s" % msg[:120]
    return False, rc, "%s（code=%s）" % (msg[:120], rc)


# 后端 → 签到实现。往后加新后端只需要三步：写一个 `do_sign_xxx(brand, env)`
# → 在这张表里注册一行 → brands.json 里给条目写 `engine`（不写 = 吾享）。
# 主流程（main）也读这张表做分派，别再往 if 链里塞。
_SIGNERS = {
    "eingdong": do_sign_yd,
    "weizulin": do_sign_wzl,
    "qmai": do_sign_qm,
    "oppo": do_sign_oppo,
}


def do_sign(brand, env):
    """返回 (是否成功, 业务码, 说明)"""
    engine = brand.get("engine") or "wuuxiang"
    if engine != "wuuxiang":
        # 非吾享后端（易东 / 微租林 …）：各家的公开接口 + 各自的身份获取，
        # 跟吾享的 mpId / token / 签名毫无关系。
        signer = _SIGNERS.get(engine)
        if not signer:
            return False, "NOENGINE", "未知后端 engine=%s（没在 _SIGNERS 里注册）" % engine
        return signer(brand, env)
    slug = brand["slug"]
    # gameId 不是凭证、是公开的活动常量，所以优先放 brands.json（这样新用户 clone 下来就有）；
    # brands/<slug>.env 里的 WX_GAMEID 优先，便于临时覆盖。
    game_id = env.get("WX_GAMEID") or brand.get("gameid", "")
    third = env.get("WX_THIRDSHOPID", "")

    # 没有 gameId 就先自己找：从活动列表里认出签到活动（对哪种类型都有用，所以不判 carrier）。
    # ⚠️ 必须放在「查会员 / 注册」**之前** —— 注册接口要 gameId。
    if not game_id:
        gid, why = discover_lot_gameid(env)
        if gid:
            game_id = gid
            env["WX_GAMEID"] = gid
            save_env(slug, env)
            log("  [discover] %s → gameId=%s" % (why, gid))
        else:
            log("  [discover] 没能从活动列表认出 gameId（%s）" % why)

    r, mem = member_info(env, game_id, third)
    code = str(r.get("code"))
    if code in (CODE_AUTH_BAD, CODE_AUTH_EXP):
        return False, code, "token 失效（%s）→ 重跑 wxrefresh.js" % r.get("msg")
    if code == CODE_NOT_MEMBER:
        # 还不是会员 → 自动注册。同一个人可在多个品牌各注册一次。
        ok, msg = register_member(brand, env)
        log("  [register] %s" % msg)
        # ⚠️ 不管注册那条路报什么，都复查一次接口：UI 脚本的退出码只能说明
        #    「流程有没有跑完」，不能证明注册结果（页面可能已经注册成功，而它没识别到）。
        time.sleep(2)
        r, mem = member_info(env, game_id, third)
        code = str(r.get("code"))
        if code != CODE_OK:
            return False, code, ("自动注册未成功（%s）→ 复查 /api/member/single 仍 code=%s msg=%s"
                                 % (msg, code, r.get("msg")))
        log("  [register] 复查通过，已是「%s」会员" % brand["name"])
    if code != CODE_OK:
        log("  [member] code=%s msg=%s" % (code, r.get("msg")))
    else:
        m = mem if isinstance(mem, dict) else {}
        for k, envk in (("id", "WX_MEMBERID"), ("cardId", "WX_CARDID"),
                        ("cardNo", "WX_CARDNO"), ("mcId", "WX_THIRDSHOPID")):
            if m.get(k) and not env.get(envk):
                env[envk] = str(m[k])
        save_env(slug, env)
        log("  [member] 会员 OK：积分=%s 卡号=%s" % (m.get("score", "-"), m.get("cardNo", "-")))

    # 签到要用 memberId/cardId/cardNo。全新自举时它们只可能来自 /api/member/single，
    # 所以这里补一次；还是拿不到就别硬发（否则服务端只会回参数错，日志还看不清原因）。
    if not (env.get("WX_MEMBERID") and env.get("WX_CARDID")):
        log("  [member] 缺会员三件套 → 再查一次 /api/member/single")
        r2, mem2 = member_info(env, game_id, third)
        m2 = mem2 if isinstance(mem2, dict) else {}
        for k, envk in (("id", "WX_MEMBERID"), ("cardId", "WX_CARDID"),
                        ("cardNo", "WX_CARDNO"), ("mcId", "WX_THIRDSHOPID")):
            if m2.get(k):
                env[envk] = str(m2[k])
        if env.get("WX_MEMBERID"):
            save_env(slug, env)
        else:
            return False, str(r2.get("code")), (
                "拿不到会员信息（memberId 为空）→ /api/member/single 返回 code=%s msg=%s"
                % (r2.get("code"), r2.get("msg")))

    # ⚠️ 签到统一走 sign 接口，**不再依赖 brands.json 的 carrier 字段**。
    # 纠正（2026-09-24 实测）：之前按「包内是否含 game/sign/signIn 字面串」判 carrier，
    # 把来菜判成了 lot —— 但它运行时走的就是 /api/game/sign/*（用逻辑层 hook wx.request
    # 抓到的真实请求：/api/game/sign/detail、/api/member/sign/survey、/api/game/sign/signIn）。
    # 所以改成**运行时判断**：先打 sign/detail，认（200）就走签；不认再退到 lot。
    if not game_id:
        return False, "-", ("缺活动 id（gameId）：sign 与 lot 接口都要先给 id，没有列举接口。"
                            "lot 型可从活动列表自动认出，sign 型得进一次签到页抄。"
                            "填到 brands.json 的 gameid 或 brands/%s.env 的 WX_GAMEID"
                            "（长期常量，填一次永久有效）" % brand["slug"])

    d = api_post(env, "/api/game/sign/detail", {"gameId": game_id})
    dc = str(d.get("code"))
    log("  [sign/detail] code=%s msg=%s" % (dc, d.get("msg")))
    if dc in (CODE_AUTH_BAD, CODE_AUTH_EXP):
        return False, dc, "token 失效（%s）→ 重跑 wxrefresh.js" % d.get("msg")
    if dc == CODE_OK:
        s = api_post(env, "/api/game/sign/signIn", {
            "gameId": game_id, "memberId": env.get("WX_MEMBERID", ""),
            "cardId": env.get("WX_CARDID", ""), "cardNo": env.get("WX_CARDNO", ""),
            "from": "", "thirdShopId": env.get("WX_THIRDSHOPID", "")})
        sc = str(s.get("code"))
        log("  [sign/signIn] code=%s msg=%s content=%s"
            % (sc, s.get("msg"), json.dumps(content_of(s), ensure_ascii=False)[:200]))
        if sc == CODE_OK:
            return True, sc, "签到成功"
        if sc == CODE_ALREADY:
            return True, sc, "今日已签到"
        if sc in (CODE_AUTH_BAD, CODE_AUTH_EXP):
            return False, sc, "token 失效"
        if sc == CODE_NOT_MEMBER:
            return False, sc, "还不是会员"
        return False, sc, "签到失败：%s" % s.get("msg")

    # sign 接口不认这个号 → 退回「活动壳」路径（lot 是抽奖/活动的通用壳，签到只是其中一种）
    log("  [sign/detail] 这个号不支持 sign 接口（code=%s）→ 退回 lot 路径" % dc)
    r, items = lot_list(env)
    log("  [lot/list] code=%s" % r.get("code"))
    act, why = pick_activity(items)
    log("  [lot/list] %s" % why)
    if act is not None:
        log("  [lot/list] 候选活动原始结构：%s" % json.dumps(act, ensure_ascii=False)[:400])
    if not game_id and act:
        for k in ("gameId", "id"):
            if act.get(k):
                env["WX_GAMEID"] = str(act[k])
                game_id = env["WX_GAMEID"]
                save_env(slug, env)
                log("  [lot/list] 已记下 gameId=%s" % game_id)
                break
    if not game_id:
        return False, "-", "没能确定活动 id（用 --discover 看原始列表，再填 WX_GAMEID）"

    # ⚠️ lot 型的「参与」接口是 /api/game/lot/check，参数结构尚未真机联调（见 README「待联调」）
    c = api_post(env, "/api/game/lot/check", {"gameId": game_id, "memberId": env.get("WX_MEMBERID", ""),
                                             "cardId": env.get("WX_CARDID", ""),
                                             "cardNo": env.get("WX_CARDNO", "")})
    cc = str(c.get("code"))
    log("  [lot/check] code=%s msg=%s content=%s"
        % (cc, c.get("msg"), json.dumps(content_of(c), ensure_ascii=False)[:200]))
    if cc == CODE_OK:
        return True, cc, "参与成功（lot 型，待复核是否为签到）"
    if cc == CODE_ALREADY:
        return True, cc, "今日已参与"
    return False, cc, "未成功：%s" % c.get("msg")


def notify(title, text, status="ok"):
    p = os.environ.get("WXSIGN_NOTIFY_PY", "")
    if p and os.path.exists(p):
        sh("%s %s --title '%s' --text '%s' --status %s" % (CPY, p, title, text, status), timeout=90)


# ───────────────────────── 主流程 ─────────────────────────

def run_brand(brand, do_ensure=False, probe=False, discover=False, register_only=False):
    slug, name = brand["slug"], brand["name"]
    log("\n===== %s（%s） appid=%s =====" % (name, slug, brand["appid"]))
    env = load_env(slug)

    # 身份（mpId/openId/unionId/gcId）与 token **全部自动获取，不需要人工抓**：
    #   开小程序（面板搜索→点卡片）→ CDP 读 storage + wx.login → /auth/login
    #   → 顺带把身份写回 brands/<slug>.env。第一次跑和以后跑走的是同一条路。
    need_ident = not (env.get("WX_MPID") and env.get("WX_OPENID"))
    if need_ident:
        log("  [init] 这个品牌还没有身份凭证 → 自动走一遍（开小程序 → 取值 → 刷 token）")
    if INSTANCE and (do_ensure or need_ident):
        ensure_helpers()
    if do_ensure or need_ident:
        if INSTANCE:
            ensure_miniapp(brand)
        elif need_ident:
            log("  [!] 缺身份且未设 WOC_INSTANCE → 无法自动开小程序取值")

    # 后端分派：非吾享后端完全不走吾享那套（mpId / token / 签名），直接进各自流程。
    # ⚠️ 必须放在 refresh_token **之前** —— 否则会被「token 刷新失败 → notoken」拦死。
    #   2026-09-25 实测踩过两次：① 易东报 RESULT code=notoken，根本到不了 do_sign_yd；
    #   ② 微租林漏注册时被拖进吾享流程 —— refresh_token 对它毫无意义地失败，还白搭一轮
    #      「关掉重开小程序 + 重启 hook」。所以这里读 _SIGNERS 表，而不是写 if 链。
    engine = brand.get("engine") or "wuuxiang"
    if engine != "wuuxiang":
        signer = _SIGNERS.get(engine)
        if not signer:
            # 兜底：engine 写错/忘了注册时明确报出来 —— 别掉进吾享流程去刷一个永远刷不出来的
            # token，那会把「配置写错」伪装成「环境故障」（上面的 ② 就是这么来的）。
            log("RESULT %s code=NOENGINE msg=未知后端 engine=%s" % (slug, engine))
            return False, "NOENGINE"
        b_ok, b_code, b_msg = signer(brand, env)
        log("RESULT %s code=%s msg=%s" % (slug, b_code, b_msg))
        return b_ok, b_code

    ok_token = refresh_token(brand, env, force=need_ident)
    if not ok_token and INSTANCE:
        # 换不到 token 最常见的原因是：小程序窗口还是上一次留下的**旧上下文**，
        # 里面的登录会话已失效 → wx.login 的 code 拿去换 token 会返 invalid code。
        # 标准动作是关掉重开，再试一次。
        log("  [retry] 换不到 token → 关掉重开小程序再试（旧上下文里的 code 会失效）")
        if ensure_miniapp(brand):
            time.sleep(3)
            ok_token = refresh_token(brand, env, force=True)
    if not ok_token and INSTANCE:
        # 第二级：只**重启 hook**（重新 attach），然后重开小程序再刷一次。
        #
        # ⚠️ 这里**故意不杀 WeChatAppEx**（`clean_leftovers(hard=True)`）—— 试过，会更糟：
        #    小程序面板本身也是 WeChatAppEx 渲染的，19 个运行时一杀，面板就再也开不起来了
        #    （实测 `[ensure] 打开失败（rc=3）→ 没能确认小程序面板`），把「读不到上下文」
        #    升级成「连面板都没了」。硬清只在**确有关不掉的残留窗口**时才用（见 ensure_miniapp）。
        #
        # 为什么值得试这一级：WMPFDebugger 只在启动时 attach，且残留窗口一多，新开的小程序
        # 可能落在没被挂上的运行时里 —— 日志表现是 `[enum] 有 wx 的上下文=[...]` 里
        # 没有目标小程序、每个 ctx 都「拿不到 mpId」，而不是「小程序没开成」。
        log("  [retry] 还是换不到 → 重启 hook（重新 attach）后再开一次")
        restart_hook()
        if ensure_miniapp(brand):
            time.sleep(3)
            ok_token = refresh_token(brand, env, force=True)
    if not ok_token:
        log("  [!] token 刷新失败 —— 可能是：① 小程序没开成；② appId 配错；"
            "③ **hook 没挂上这个运行时**（看上面 [enum]/[try] 里有没有目标 appId 的 ctx）；"
            "④ **小程序自己还没登录**（storage 里没有带 mpid 的 token，"
            "常见于小程序停在隐私弹层/未点「我的」）。③④ 脚本都已自动重试过一轮。")
        log("RESULT %s code=notoken msg=token 不可用" % slug)
        return False, "notoken"
    env = load_env(slug)          # 刷新后重新读（身份可能刚被写进来）

    if not env.get("WX_MPID"):
        log("  [!] 仍拿不到 mpId：小程序没开成，或 appId 配错了（当前 %s）" % brand["appid"])
        log("RESULT %s code=noident msg=拿不到身份" % slug)
        return False, "noident"

    # 缺 gameId 时补抓一次（wxident.js 会读页面 data 取 gameId）。
    # 不判 carrier：那个分类已证伪（来菜包里没有 sign 字面串，运行时走的却是 sign 接口）。
    if not (env.get("WX_GAMEID") or brand.get("gameid")):
        mp_before = env.get("WX_MPID", "")
        harvest_ident(brand)
        env = load_env(slug)
        # ⚠️ harvest_ident 可能**改写 mpId**（它是从登录 token 的 payload 里取的，
        #    比 wxrefresh 早一步拿到真值）。而 refresh_token 是在它**之前**跑的，
        #    于是就会出现「token 属于旧租户、mpId 是新的」→ 之后所有接口返
        #    `208 授权码错误`，表现得像「这个号没有活动」（假结论，实测踩过 3 个号）。
        #    mpId 一变就强制重换 token。
        if env.get("WX_MPID") and env.get("WX_MPID") != mp_before:
            log("  [init] mpId 更新为 %s（原 %s）→ 强制重换 token（否则接口会返 208）"
                % (env["WX_MPID"], mp_before or "(空)"))
            if refresh_token(brand, env, force=True):
                env = load_env(slug)


    if discover:
        r, items = lot_list(env)
        log("  [discover] code=%s" % r.get("code"))
        log(json.dumps(items, ensure_ascii=False, indent=1)[:3000])
        return True, "discover"

    # ⚠️ 顺序要紧：`--probe` 必须在 `--register` 之前判。两个一起给时语义是
    #    「探测，并且需要注册就给这个号注册后再终判」（见下面 probe 分支里的 register_only）。
    if probe:
        r, mem = member_info(env, env.get("WX_GAMEID", ""), env.get("WX_THIRDSHOPID", ""))
        # 兜底：token 与租户不匹配（208/211）就强制重换一次再问。
        # 208 是「授权码错误」——token 签发时的 mpid 与我们现在发的不一致。
        # 不处理的话后面每条接口都返 208，最后会走进「服务端没有活动」那个 else，
        # 把「没答上」写成「查过了、没有」（实测污染过 3 个号）。
        if str(r.get("code")) in (CODE_AUTH_BAD, CODE_AUTH_EXP):
            log("  [auth] 接口返 %s（%s）→ token 与租户不匹配，强制刷新后重试"
                % (r.get("code"), str(r.get("msg"))[:40]))
            if refresh_token(brand, env, force=True):
                env = load_env(slug)
                r, mem = member_info(env, env.get("WX_GAMEID", ""), env.get("WX_THIRDSHOPID", ""))
        log("  [probe·member] code=%s content=%s"
            % (r.get("code"), json.dumps(mem, ensure_ascii=False)[:300]))
        r2, items = lot_list(env)
        log("  [probe·lot/list] code=%s content=%s"
            % (r2.get("code"), json.dumps(items, ensure_ascii=False)[:600]))

        # 「这个号能不能签到」的判据演进过四轮（每轮都是踩出来的）：
        #   ✗ 包内有 pages/sign / game/sign 字面量 → 只证明**壳**有能力，证不了租户开了活动
        #   ✗ /api/game/lot/list 200               → 辣可可（sign 型）这里也返 405，会漏掉它
        #   ✗ /api/game/sign/detail 200            → **只说明这个 gameId 有效**！
        #        实测蜀大侠（周三会员日抽奖）、农耕记（周四秒杀）的 sign/detail 也是 200。
        #   ✗ /api/member/sign/survey 200          → **只对「已经是该品牌会员」的号成立**。
        #        实测过桥缘游戏中心：活动就叫「过桥缘签到送积分」、type=2、sign/detail 200，
        #        但还没注册会员 → survey 返 411，被判成「不是签到」——**假阴性**，
        #        真跑一遍自动注册后立刻签到成功（415 今日已签到）。
        #   ✓ sign/detail 的 **isCumulativeSign 非 null** → 不依赖会员资格，且能区分：
        #        过桥缘=0（是签到，非累积型）；蜀大侠/农耕记=null（抽奖/秒杀，不是签到）。
        #   所以判据是：先看 isCumulativeSign 定性，能用 survey 定量时才用 survey。
        acts = items.get("list") if isinstance(items, dict) else None
        gid = env.get("WX_GAMEID") or brand.get("gameid") or ""
        if not gid and acts:
            gid = str(acts[0].get("id") or "")
        # ⚠️ 传输失败 ≠ 业务结论。
        #    实测：批量跑时本机临时端口耗尽（WinError 10048）+ 出口隧道 502，
        #    所有调用都返 code=-1，于是走到最后那个 else，被写成「服务端没有活动（该租户未配置）」
        #    ——**这是个假结论**，会把「没查到」伪装成「查过了、没有」。
        #    凡是本号出现过传输失败，就一律标成不可判定，交给重跑。
        net_bad = is_transport_fail(r) or is_transport_fail(r2)
        if not gid:
            if is_transport_fail(r2):
                log("  [probe] ⚠️ 传输失败（%s）—— **未判定**，请重跑这个号"
                    % str(r2.get("msg"))[:70])
                return False, "netfail"
            # ⚠️ 这里**不能**说「没有签到活动」。
            #    `lot/list` 是**抽奖/游戏**的活动列表，实测辣可可（确实有签到活动
            #    「可可会员签到」）在这里也返 405 —— 所以「lot/list 非 200」放不出任何
            #    关于签到的话。加之签到接口要 gameId，而 gameId 只能从小程序页面 data 里抓。
            #    成员身份是唯一可用的旁证：已是会员却看不到任何活动，值得存疑。
            if str(r.get("code")) == CODE_OK:
                log("  [probe] ⚠️ **未判定**：已是该品牌会员，但 lot/list=%s 且抓不到 gameId。"
                    "这类租户的签到活动不在 lot/list 里（辣可可就如此）—— "
                    "别据此判「不能签」，要进去读签到页的 gameId。" % r2.get("code"))
                return False, "nogameid"
            log("  [probe] ⚪ 未发现活动（lot/list=%s，且抓不到 gameId）—— "
                "注意：这只排除「抽奖类活动」，**签到活动不在 lot/list 里**，"
                "所以这个结论对签到是**弱结论**。" % r2.get("code"))
            return True, "probe"

        is_member = str(r.get("code")) == CODE_OK
        log("  [probe·member] code=%s → %s"
            % (r.get("code"), "已是会员" if is_member else "还不是会员（survey 还没法答）"))
        # ⚠️ 是会员就**当场把会员三件套写回 env**再打 survey。
        #    不写回的话：member/single 说「是会员」，但 env 里 memberId 还是空的，
        #    sign/survey 就会拿空参数去问 → 又返 411 → 又被误判成「不是签到」
        #    （实测踩过：过桥缘系的另两个号 new48/new49 就是这样被判成 🟡 的）。
        #    member/single 与 sign/survey 用的是**两套参数**，这一层很容易漏。
        if is_member and isinstance(mem, dict) and not env.get("WX_MEMBERID"):
            for k, envk in (("id", "WX_MEMBERID"), ("cardId", "WX_CARDID"),
                            ("cardNo", "WX_CARDNO"), ("mcId", "WX_THIRDSHOPID")):
                if mem.get(k):
                    env[envk] = str(mem[k])
            save_env(slug, env)
            log("  [probe·member] 已把会员三件套写回 env（memberId/cardId/cardNo）")

        sd = api_post(env, "/api/game/sign/detail", {"gameId": gid})
        det = content_of(sd) or {}
        name = str(det.get("name"))[:30] if isinstance(det, dict) else ""
        cum = det.get("isCumulativeSign") if isinstance(det, dict) else None
        log("  [probe·sign/detail] gameId=%s code=%s name=%s isCumulativeSign=%s"
            % (gid, sd.get("code"), name, cum))

        is_sign_act = str(sd.get("code")) == CODE_OK and cum is not None

        sv = api_post(env, "/api/member/sign/survey", {
            "gameId": gid, "memberId": env.get("WX_MEMBERID", ""),
            "cardId": env.get("WX_CARDID", ""), "cardNo": env.get("WX_CARDNO", "")})
        svi = content_of(sv) or {}
        log("  [probe·sign/survey] code=%s content=%s"
            % (sv.get("code"), json.dumps(svi, ensure_ascii=False)[:200]))

        if str(sv.get("code")) == CODE_OK:
            log("  [probe] ✅ 能签到：%s（signNum=%s）"
                % (name, svi.get("signNum") if isinstance(svi, dict) else "-"))
            return True, "probe"
        net_bad = net_bad or is_transport_fail(sd) or is_transport_fail(sv)

        if is_sign_act and register_only:
            # `--probe --register`：不是会员时先注册，再复查一次，给终判（会真实建会员！）
            ok, msg = register_member(brand, env)
            log("  [probe·register] %s" % msg)
            if ok:
                time.sleep(2)
                r3, mem3 = member_info(env, gid, env.get("WX_THIRDSHOPID", ""))
                m3 = mem3 if isinstance(mem3, dict) else {}
                for k, envk in (("id", "WX_MEMBERID"), ("cardId", "WX_CARDID"),
                                ("cardNo", "WX_CARDNO"), ("mcId", "WX_THIRDSHOPID")):
                    if m3.get(k):
                        env[envk] = str(m3[k])
                save_env(slug, env)
                sv2 = api_post(env, "/api/member/sign/survey", {
                    "gameId": gid, "memberId": env.get("WX_MEMBERID", ""),
                    "cardId": env.get("WX_CARDID", ""), "cardNo": env.get("WX_CARDNO", "")})
                log("  [probe·sign/survey] 注册后复查 code=%s content=%s"
                    % (sv2.get("code"), json.dumps(content_of(sv2) or {}, ensure_ascii=False)[:200]))
                if str(sv2.get("code")) == CODE_OK:
                    log("  [probe] ✅ 能签到（注册后确认）：%s" % name)
                else:
                    log("  [probe] 🟡 有签到活动，但注册后 survey 仍返 %s —— "
                        "多半该租户没配会员卡 / 签到档期未到" % sv2.get("code"))
            return True, "probe"

        if is_sign_act and not is_member:
            log("  [probe] 🔵 **是签到活动**（%s，isCumulativeSign=%s）—— 只是这个号还不是会员，"
                "survey 答不了（%s）。加 --register 可自动注册后给终判。"
                % (name, cum, sv.get("code")))
        elif is_sign_act:
            log("  [probe] 🟡 有签到活动（%s），但 survey 返 %s —— 多半没配会员卡" % (name, sv.get("code")))
        elif str(sd.get("code")) == CODE_OK:
            log("  [probe] 🟡 有活动但**不是签到**（isCumulativeSign 为 null）：%s —— "
                "这类号只能参与抽奖/秒杀，签不了" % name)
        elif net_bad:
            log("  [probe] ⚠️ 传输失败 —— **未判定**（接口返 code=-1，属本机/链路问题，"
                "不是「没有活动」）。请重跑这个号。")
            return False, "netfail"
        elif str(sd.get("code")) in (CODE_AUTH_BAD, CODE_AUTH_EXP) \
                or str(sv.get("code")) in (CODE_AUTH_BAD, CODE_AUTH_EXP):
            log("  [probe] ⚠️ 鉴权失败（208/211）—— **未判定**，不是「没有活动」。"
                "重跑一次即可（脚本会重换 token）。")
            return False, "authfail"
        else:
            log("  [probe] ⚪ 这个 gameId（%s）查不到活动（sign/detail=%s）—— "
                "**只说明这个 id 上没活动**，不等于该租户没有别的签到活动。" % (gid, sd.get("code")))
        return True, "probe"

    if register_only:
        # 只注册（`--probe --register` 已被上面的 probe 分支接管，不会走到这）
        ok, msg = register_member(brand, env)
        log("  [register] %s" % msg)
        if ok:
            r, mem = member_info(env, env.get("WX_GAMEID", ""), env.get("WX_THIRDSHOPID", ""))
            log("  [register] 复查 /api/member/single → code=%s content=%s"
                % (r.get("code"), json.dumps(mem, ensure_ascii=False)[:300]))
        return ok, "register"

    ok, code, msg = do_sign(brand, env)
    log("RESULT %s code=%s msg=%s" % (slug, code, msg))
    return ok, code


def main():
    argv = sys.argv[1:]
    if "--find" in argv:
        i = argv.index("--find")
        if i + 1 >= len(argv):
            log("用法：wxsign.py --find <品牌关键词>   —— 自动搜出该品牌名下的小程序并读出 appId")
            return 2
        return 0 if discover_appid(argv[i + 1]) else 1
    if not argv or "--list" in argv:
        cfg = load_brands()
        log("%-16s %-10s %-22s %-22s %s" % ("slug", "品牌", "载体小程序", "appid", "状态"))
        for b in cfg["brands"]:
            log("%-16s %-10s %-22s %-22s %s%s"
                % (b["slug"], b["name"], b["miniapp"], b["appid"],
                   "启用" if b.get("enabled") else "停用",
                   "（已联调）" if b.get("verified") else ""))
        log("")
        for line in resolve_targets(cfg, argv)[1]:
            log("  " + line)
        log("\n用法：wxsign.py --find <关键词>                     自动发现该品牌的 appId（不用手抄）")
        log("      wxsign.py <slug> [--probe|--discover|--register|--ensure]")
        log("      wxsign.py --all [--ensure]                    所有 enabled 品牌")
        log("      wxsign.py --apps a,b [--exclude c]            只签 a、b（可多选），不签 c（可多选）")
        log("      wxsign.py <slug> --reset-identity             换了微信账号后清掉旧凭证")
        log("      <slug> 不带参数 = 直接签到（不是会员会自动注册，见 --register）")
        log("\n  签哪几个也可以用环境变量配（青龙「环境变量」页）：")
        log("      WXSIGN_APPS=a,b        白名单，逗号分隔的多选；不配 = all")
        log("      WXSIGN_EXCLUDE=c,d     黑名单，优先级最高，谁都排除得掉")
        return 0

    # 换微信账号（小号验证完换大号）后，旧账号的 openId 等会一直粘在 env 里，
    # 症状是「抓不到身份 / invalid code」且看不出跟换号有关 —— 用这条清掉。
    if "--reset-identity" in argv:
        cfg = load_brands()
        targets, scope = resolve_targets(cfg, argv)
        if not targets:
            log("用法：wxsign.py <slug> --reset-identity   # 清该品牌的账号凭证")
            log("      wxsign.py --all --reset-identity   # 所有启用的品牌")
            log("      （加 --dry-run 只看会清什么，不写文件）")
            return 2
        for line in scope:
            log("  " + line)
        log("换微信账号后要清掉这些字段：它们是绑在账号上的，且脚本不会自动覆盖。")
        for b in targets:
            reset_identity(b["slug"], dry_run="--dry-run" in argv)
        log("\n清完 %d 个品牌。接着跑一次就会重新抓身份：" % len(targets))
        log("  python3 wxsign.py --all --ensure")
        log("  新账号在各品牌都不是会员 → 会自动走一遍注册（微信授权弹窗，不用手机号）。")
        return 0

    cfg = load_brands()
    do_ensure = "--ensure" in argv
    probe = "--probe" in argv
    discover = "--discover" in argv
    register_only = "--register" in argv

    # 默认 all（enabled 的），可用白名单收窄、黑名单排除 —— 见 resolve_targets()
    targets, scope = resolve_targets(cfg, argv)
    for line in scope:
        log("  " + line)
    if not targets:
        log("[ERROR] 本次没有要跑的小程序 —— 看上面「范围 / 排除」两行。")
        log("        可选 slug：%s" % "、".join(b["slug"] for b in cfg["brands"]))
        return 2

    okc = failc = 0
    lines = []
    for b in targets:
        try:
            ok, code = run_brand(b, do_ensure=do_ensure, probe=probe, discover=discover,
                                 register_only=register_only)
        except Exception as e:
            ok, code = False, "exception"
            log("  [!] 异常：%s" % e)
        lines.append("%s %s %s" % ("✅" if ok else "❌", b["name"], code))
        okc += 1 if ok else 0
        failc += 0 if ok else 1

    log("\n========== 汇总 ==========")
    for l in lines:
        log("  " + l)
    log("成功 %d / 失败 %d" % (okc, failc))
    if not probe and not discover:
        notify("吾享签到合集", "\n".join(lines) + "\n时间: %s" % time.strftime("%F %T"),
               "ok" if failc == 0 else "fail")
    return 0 if failc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
