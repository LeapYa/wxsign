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
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
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

def api_post(env, path, data, retries=2):
    """带重试的 POST。实测沙箱/容器出口偶发 `502 Bad Gateway`（Tunnel connection failed），
    这类是网络层瞬时错误、重试就好，不该直接判业务失败。"""
    r = None
    for attempt in range(retries + 1):
        r = _api_post_once(env, path, data)
        if str(r.get("code")) != "-1":
            return r
        if attempt < retries:
            log("  [net] %s 第 %d 次失败（%s），%.1fs 后重试"
                % (path, attempt + 1, str(r.get("msg"))[:60], 1.5 * (attempt + 1)))
            time.sleep(1.5 * (attempt + 1))
    return r


def _api_post_once(env, path, data):
    mp = env.get("WX_MPID", "")
    if not mp:
        return {"code": "-1", "msg": "缺少 WX_MPID（先跑 wxident.js 抓身份）"}
    body = {"mpId": mp, "openId": env.get("WX_OPENID", ""),
            "unionId": env.get("WX_UNIONID", ""), "data": data}
    headers = {"Content-Type": "application/json",
               "Authorization": env.get("WX_TOKEN", ""),
               "crm7-mpId": mp, "User-Agent": UA}
    if env.get("WX_GCID"):
        headers["csl-GC-Shardingkey"] = env["WX_GCID"]
    req = urllib.request.Request(CRM_BASE + path, data=json.dumps(body).encode(),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20, context=_SSL) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace") if e.fp else ""
        return {"code": str(e.code), "msg": "HTTP %s: %s" % (e.code, raw[:200])}
    except Exception as e:
        return {"code": "-1", "msg": str(e)}


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


def clean_leftovers():
    """关掉残留的小程序窗口（含清掉挡住关闭按钮的隐私弹窗）。

    为什么必须做：① 残留窗口会盖住微信侧边栏，面板可能认不出来；
    ② 更要紧的是 —— **旧窗口里的登录会话会失效**，此时 wx.login() 取到的 code
    拿去 /auth/login 会返 `invalid code`。所以「换不到 token」时的标准动作就是
    「关掉重开」。
    """
    if not INSTANCE:
        return False
    try:
        c = subprocess.run(["docker", "exec", "-e", "DISPLAY=:1", INSTANCE,
                            CPY, cpath(CTMP, "wxclean.py")],
                           capture_output=True, text=True, timeout=300)
    except Exception as e:
        log("  [clean] 跳过（%s）" % e)
        return False
    for line in ((c.stdout or "") + (c.stderr or "")).splitlines():
        if line.startswith("[clean]"):
            log("  " + line)
    if c.returncode not in (0, 1):
        log("  [clean] rc=%s（不影响后续，继续试）" % c.returncode)
    return c.returncode == 0


def ensure_miniapp(brand):
    """把该品牌的「载体小程序」打开（token 的 jsCode 与 appId 绑死，必须开对号）。"""
    if not INSTANCE:
        log("  [ensure] 未设 WOC_INSTANCE，跳过自动开小程序")
        return False
    clean_leftovers()

    # 用参数列表而非拼字符串：小程序名里有中文，拼命令行容易被引号吃掉
    args = ["docker", "exec", "-e", "DISPLAY=:1",
            "-e", "WXSIGN_MINIAPP=" + brand["miniapp"],
            "-e", "WXSIGN_KEYWORD=" + brand["keyword"],
            INSTANCE, CPY, cpath(CTMP, "wxopen.py"), "--loose"]
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=300)
        rc, out = p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        rc, out = 1, str(e)
    # reopen_miniapp 的 --loose 语义：0=已打开；4=点过候选但没法用窗口标题确认（交给 appId 复核）
    ok = rc in (0, 4)
    log("  [ensure] %s → rc=%s %s" % (brand["miniapp"], rc, "(已就绪)" if ok else "(可能没开成)"))
    for line in out.splitlines():
        # 出错了就把所有输出都打出来 —— 只过滤 [reopen] 会把真正的报错吞掉（踩过）
        if line.startswith("[reopen]") or (not ok and line.strip()):
            log("     " + line[:200])
    return ok


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
    srcs = [(os.path.join(HERE, f), cpath(CTMP, f)) for f in
            ("wxfind.py", "wxcdp.py", "wxdom.py", "wxwin.py", "pkgprobe.py",
             "wxclean.py", "wxopen.py", "wxreg.py")]
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
    args = ["docker", "exec", "-e", "DISPLAY=:1", INSTANCE, CPY,
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


def do_sign(brand, env):
    """返回 (是否成功, 业务码, 说明)"""
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

    ok_token = refresh_token(brand, env, force=need_ident)
    if not ok_token and INSTANCE:
        # 换不到 token 最常见的原因是：小程序窗口还是上一次留下的**旧上下文**，
        # 里面的登录会话已失效 → wx.login 的 code 拿去换 token 会返 invalid code。
        # 标准动作是关掉重开，再试一次。
        log("  [retry] 换不到 token → 关掉重开小程序再试（旧上下文里的 code 会失效）")
        if ensure_miniapp(brand):
            time.sleep(3)
            ok_token = refresh_token(brand, env, force=True)
    if not ok_token:
        log("  [!] token 刷新失败——小程序没开成，或 appId 配错了（当前 %s）" % brand["appid"])
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
        harvest_ident(brand)
        env = load_env(slug)


    if discover:
        r, items = lot_list(env)
        log("  [discover] code=%s" % r.get("code"))
        log(json.dumps(items, ensure_ascii=False, indent=1)[:3000])
        return True, "discover"

    if register_only:
        ok, msg = register_member(brand, env)
        log("  [register] %s" % msg)
        if ok:
            r, mem = member_info(env, env.get("WX_GAMEID", ""), env.get("WX_THIRDSHOPID", ""))
            log("  [register] 复查 /api/member/single → code=%s content=%s"
                % (r.get("code"), json.dumps(mem, ensure_ascii=False)[:300]))
        return ok, "register"

    if probe:
        r, mem = member_info(env, env.get("WX_GAMEID", ""), env.get("WX_THIRDSHOPID", ""))
        log("  [probe·member] code=%s content=%s"
            % (r.get("code"), json.dumps(mem, ensure_ascii=False)[:300]))
        r2, items = lot_list(env)
        log("  [probe·lot/list] code=%s content=%s"
            % (r2.get("code"), json.dumps(items, ensure_ascii=False)[:600]))

        # 「这个号能不能签到」要看**能不能拿到签到记录**，判据演进过三轮：
        #   ✗ 包内有 pages/sign / game/sign 字面量 → 只证明**壳**有能力，证不了租户开了活动
        #   ✗ /api/game/lot/list 200               → 辣可可（sign 型）这里也返 405，会漏掉它
        #   ✗ /api/game/sign/detail 200            → **只说明这个 gameId 有效**！
        #        实测蜀大侠（周三会员日抽奖）、农耕记（周四秒杀）的 sign/detail 也是 200，
        #        但它们的 isCumulativeSign = null、sign/survey 返 406 —— **不是签到活动**。
        #   ✓ /api/member/sign/survey 200          → 能拿到 signNum / lastSignDate 才是真能签
        acts = items.get("list") if isinstance(items, dict) else None
        gid = env.get("WX_GAMEID") or brand.get("gameid") or ""
        if not gid and acts:
            gid = str(acts[0].get("id") or "")
        if not gid:
            log("  [probe] ⚪ 服务端没有活动，也没有可用的 gameId（lot/list=%s）" % r2.get("code"))
            return True, "probe"

        sd = api_post(env, "/api/game/sign/detail", {"gameId": gid})
        det = content_of(sd) or {}
        name = str(det.get("name"))[:30] if isinstance(det, dict) else ""
        log("  [probe·sign/detail] gameId=%s code=%s name=%s isCumulativeSign=%s"
            % (gid, sd.get("code"), name,
               det.get("isCumulativeSign") if isinstance(det, dict) else "-"))

        sv = api_post(env, "/api/member/sign/survey", {
            "gameId": gid, "memberId": env.get("WX_MEMBERID", ""),
            "cardId": env.get("WX_CARDID", ""), "cardNo": env.get("WX_CARDNO", "")})
        svi = content_of(sv) or {}
        log("  [probe·sign/survey] code=%s content=%s"
            % (sv.get("code"), json.dumps(svi, ensure_ascii=False)[:200]))

        if str(sv.get("code")) == CODE_OK:
            log("  [probe] ✅ 能签到：%s（signNum=%s）"
                % (name, svi.get("signNum") if isinstance(svi, dict) else "-"))
        elif str(sd.get("code")) == CODE_OK:
            log("  [probe] 🟡 有活动但**不是签到**（sign/survey 非 200）：%s —— "
                "这类号只能参与抽奖/秒杀，签不了" % name)
        else:
            log("  [probe] ⚪ 服务端没有活动（该租户未配置）")
        return True, "probe"

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
