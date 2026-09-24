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
    python3 <脚本目录>/wxsign.py --list              看品牌表
    python3 <脚本目录>/wxsign.py <slug> --probe      只探测（会员 + 活动列表），不签到
    python3 <脚本目录>/wxsign.py <slug> --discover   把该租户所有活动的原始 JSON 打出来
    python3 <脚本目录>/wxsign.py <slug> --register   只做会员注册（不是会员时用）
    python3 <脚本目录>/wxsign.py <slug>              该品牌签到（不是会员会自动注册再签）
    python3 <脚本目录>/wxsign.py --all [--ensure]    所有 enabled 品牌（--ensure 会先开小程序并刷 token）

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


def jwt_exp(tok):
    import base64
    try:
        p = tok.split(".")[1]
        p += "=" * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p)).get("exp")
    except Exception:
        return None


# ───────────────────────── HTTP ─────────────────────────

def api_post(env, path, data):
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


def ensure_miniapp(brand):
    """把该品牌的「载体小程序」打开（token 的 jsCode 与 appId 绑死，必须开对号）。"""
    if not INSTANCE:
        log("  [ensure] 未设 WOC_INSTANCE，跳过自动开小程序")
        return False
    # 先清残留的小程序窗口：上一次没关掉的窗口会盖在微信上，导致侧边栏点不中、面板认不出
    # （实测：残留窗口上有隐私弹窗/悬浮提示时，关闭按钮点不中）
    try:
        c = subprocess.run(["docker", "exec", "-e", "DISPLAY=:1", INSTANCE,
                            CPY, cpath(CTMP, "wxclean.py")],
                           capture_output=True, text=True, timeout=300)
        for line in ((c.stdout or "") + (c.stderr or "")).splitlines():
            if line.startswith("[clean]") or c.returncode not in (0, 1):
                log("  " + line)
        if c.returncode not in (0, 1):
            log("  [clean] rc=%s（不影响后续，继续试）" % c.returncode)
    except Exception as e:
        log("  [clean] 跳过（%s）" % e)

    # 用参数列表而非拼字符串：小程序名里有中文，拼命令行容易被引号吃掉
    args = ["docker", "exec", "-e", "DISPLAY=:1",
            "-e", "LAKEKE_MINIAPP=" + brand["miniapp"],
            "-e", "LAKEKE_KEYWORD=" + brand["keyword"],
            INSTANCE, CPY, cpath(CTMP, "reopen_miniapp.py"), "--loose"]
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

    手机号来源（按优先级）：
      1. brands/<slug>.env 的 WX_REGISTER_PHONE（单品牌专属号）
      2. 进程环境变量 WXSIGN_REGISTER_PHONE（青龙里配一次，所有品牌共用）
    方式 WX_REGISTER_MODE：api（默认，有号就直连注册）| ui（走微信授权弹窗）| auto
    同一个人可以在多个品牌各注册一次会员，互不影响。
    """
    mode = (env.get("WX_REGISTER_MODE") or os.environ.get("WXSIGN_REGISTER_MODE") or "auto").lower()
    phone = (env.get("WX_REGISTER_PHONE") or os.environ.get("WXSIGN_REGISTER_PHONE") or "").strip()
    if mode == "auto":
        mode = "api" if phone else "ui"
    log("  [register] 还不是会员 → 方式=%s 手机号=%s"
        % (mode, "已设" if phone else "(未设)"))

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

    # UI 兜底：驱动微信的手机号授权弹窗。脚本在各项目里都不一样，所以用环境变量指过去。
    ui_py = os.environ.get("WXSIGN_UI_REGISTER_PY", "")
    if not ui_py or not os.path.exists(ui_py):
        return False, ("UI 注册需要 WXSIGN_UI_REGISTER_PY 指向 ui_register.py"
                       "（辣可可项目里那份，会点微信手机号授权弹窗）")
    if not INSTANCE:
        return False, "UI 注册需要 WOC_INSTANCE（要操作微信界面）"
    inner = ("cd %s && SHOT_DIR=%s REG_PHONE_INDEX=%s REG_PHONE=%s python3 %s"
             % (CTMP, CTMP + "/shots",
                env.get("WX_REGISTER_PHONE_INDEX", "0") or "0", phone,
                cpath(CTMP, "ui_register.py")))
    try:
        subprocess.run(["docker", "cp", ui_py, "%s:%s" % (INSTANCE, cpath(CTMP, "ui_register.py"))],
                       capture_output=True, text=True, timeout=120)
        p = subprocess.run(["docker", "exec", "-e", "DISPLAY=:1", "-e", "SHOT_DIR=" + CTMP + "/shots",
                            INSTANCE, "sh", "-c", inner],
                           capture_output=True, text=True, timeout=900)
        for line in ((p.stdout or "") + (p.stderr or "")).splitlines()[-25:]:
            log("     " + line[:200])
    except Exception as e:
        return False, "UI 注册异常：%s" % e
    return True, "UI 注册流程已跑（结果看上面的日志/截图）"


def pick_activity(items, want=("签到", "打卡", "sign")):
    """从活动列表里挑签到类活动（type/名称命中关键词）。返回 (activity, 命中理由)。"""
    if isinstance(items, dict):
        for k in ("list", "records", "rows", "content", "data"):
            if isinstance(items.get(k), list):
                items = items[k]
                break
    if not isinstance(items, list):
        return None, "活动列表结构未识别"
    for it in items:
        if not isinstance(it, dict):
            continue
        blob = " ".join(str(it.get(k, "")) for k in
                        ("type", "gameType", "name", "title", "activityName", "remark", "status"))
        for w in want:
            if w in blob:
                return it, "命中「%s」" % w
    return None, "列表里没有签到类活动（共 %d 个）" % len(items)


def do_sign(brand, env):
    """返回 (是否成功, 业务码, 说明)"""
    slug = brand["slug"]
    game_id = env.get("WX_GAMEID", "")
    third = env.get("WX_THIRDSHOPID", "")

    r, mem = member_info(env, game_id, third)
    code = str(r.get("code"))
    if code in (CODE_AUTH_BAD, CODE_AUTH_EXP):
        return False, code, "token 失效（%s）→ 重跑 wxrefresh.js" % r.get("msg")
    if code == CODE_NOT_MEMBER:
        # 还不是会员 → 自动注册（照抄页面源码的 registerVip）。同一个人可在多个品牌各注册一次。
        ok, msg = register_member(brand, env)
        log("  [register] %s" % msg)
        if not ok:
            return False, code, "「%s」自动注册未成功：%s" % (brand["name"], msg)
        time.sleep(2)
        r, mem = member_info(env, game_id, third)
        code = str(r.get("code"))
        if code != CODE_OK:
            return False, code, "注册后复查 /api/member/single 仍失败（code=%s msg=%s）" % (
                code, r.get("msg"))
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

    # 该号包内有专用签到模块 → 走最稳的 sign 接口
    if brand.get("carrier") == "sign" and game_id:
        d = api_post(env, "/api/game/sign/detail", {"gameId": game_id})
        dc = str(d.get("code"))
        log("  [sign/detail] code=%s msg=%s" % (dc, d.get("msg")))
        if dc in (CODE_AUTH_BAD, CODE_AUTH_EXP):
            return False, dc, "token 失效"
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

    # 只有活动壳（lot）的号：先列活动，把签到类活动的原始结构打出来供接入
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
    if not env.get("WX_MPID"):
        log("  [!] 还没有 %s —— 先把小程序开着，跑一次身份采集："
            "\n      docker exec %s sh -c 'cd %s && ENVFILE=%s WX_APPID=%s "
            "NODE_PATH=/opt/wmpf/node_modules node wxident.js 60'"
            % (os.path.relpath(env_path(slug), HOME), HOOK, HOME_C,
               os.path.join(HOME_C, "brands", "%s.env" % slug), brand["appid"]))
        log("RESULT %s code=noident msg=缺身份配置" % slug)
        return False, "noident"

    if do_ensure:
        ensure_miniapp(brand)
    if not refresh_token(brand, env):
        log("  [!] token 刷新失败——小程序没开或 appId 不对")
        log("RESULT %s code=notoken msg=token 不可用" % slug)
        return False, "notoken"
    env = load_env(slug)          # 刷新后重新读

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
        return True, "probe"

    ok, code, msg = do_sign(brand, env)
    log("RESULT %s code=%s msg=%s" % (slug, code, msg))
    return ok, code


def main():
    argv = sys.argv[1:]
    if not argv or "--list" in argv:
        cfg = load_brands()
        log("%-16s %-10s %-22s %-22s %s" % ("slug", "品牌", "载体小程序", "appid", "状态"))
        for b in cfg["brands"]:
            log("%-16s %-10s %-22s %-22s %s%s"
                % (b["slug"], b["name"], b["miniapp"], b["appid"],
                   "启用" if b.get("enabled") else "停用",
                   "（已联调）" if b.get("verified") else ""))
        log("\n用法：wxsign.py <slug> [--probe|--discover|--register|--ensure]  |  wxsign.py --all [--ensure]")
        log("      <slug> 不带参数 = 直接签到（不是会员会自动注册，见 --register）")
        return 0

    cfg = load_brands()
    by_slug = {b["slug"]: b for b in cfg["brands"]}
    do_ensure = "--ensure" in argv
    probe = "--probe" in argv
    discover = "--discover" in argv
    register_only = "--register" in argv

    if "--all" in argv:
        targets = [b for b in cfg["brands"] if b.get("enabled")]
    else:
        slugs = [a for a in argv if not a.startswith("--")]
        targets = [by_slug[s] for s in slugs if s in by_slug]
        if not targets:
            log("[ERROR] 不认识的品牌 slug：%s" % slugs)
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
