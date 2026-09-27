#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""蜜雪冰城「雪王币抽奖」（兑吧 duiba 大转盘）—— 纯 HTTP，含 JS-challenge token。

## 定位
这是 wxsign 引擎的第 6 个后端：`engine = "duiba"`。
与其它后端最大的不同：**兑吧的写接口要一个 JS-challenge token**，
所以本模块需要用 **Node** 在沙箱里执行服务端下发的两段 JS（见 NODE_RUNNER）。

## 完整链路（全部来自解包/抓包，非猜测）
```
① 蜜雪 accessToken（容器内 CDP 读 getApp().globalData.accessToken）
② GET  https://mxsa.mxbc.net/api/v1/duiba/getLoginUrl?dbredirect=<活动URL>
     → data.loginUrl = <活动域>/autoLogin/autologin?…&uid=…&sign=…   （免密登录）
③ 访问 loginUrl → 拿 dexfu 会话 cookie（wdata3 / wdata4 / tokenId …）
④ GET  <活动>/galaxy/app/project/2924/luck/index.do   ← **不需要 token**
     → remainFreeTimes（今天还剩几次免费）/ hadPlayTime / credits / preConsumeCredits
⑤ token：兑吧 JS-challenge
     GET  …/getTokenKey.query?projectId=2924   → JS1
     GET  …/getToken.query?projectId=2924      → JS2
     原码：把 JS 塞进 <script> 注入 document.head 执行，再调 window.<新函数>() 取 token
⑥ GET  …/luck/draw.do?ticketNum=&token=<t>     抽奖
```

## ⚠️ 最重要的一条：**不能在 remainFreeTimes=0 时抽**
免费次数用完后，`draw.do` 会**扣 20 雪王币**继续抽（规则原文如此）。
所以本模块**一律先查 `remainFreeTimes`，>0 才调 draw** —— 否则会白花雪王币。

## 签名（蜜雪侧，utils/enhanceMD5.js）
    params 补 appId / t(ms) / s=3
    sign = md5( key 升序 "k=v&k=v" + salt_weixin ) + 「4 组有符号大端 int 绝对值」拼接

## 用法
    # 作为 wxsign 后端（自动）：brands.json 里写 "engine": "duiba"
    # CLI：
    python3 mx_lottery.py --probe     # 只查状态（不抽奖）
    python3 mx_lottery.py --draw      # 有免费次数才抽
    python3 mx_lottery.py --dump-js <dir>
"""
import argparse
import hashlib
import http.cookiejar
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

BASE = "https://mxsa.mxbc.net/api"
VER = "2.8.58"
SALT_WEIXIN = "0d787c102fe2f7b4279af8925819d5fd"
APPID_WEIXIN = "d82be6bbc1da11eb9dd000163e122ecb"
APPID = "wx7696c66d2245d107"

ACT_HOST = "https://76177-activity.dexfu.cn"
PROJECT = 2924
ACT_URL = "%s/galaxy/app/project/%d/index.html" % (ACT_HOST, PROJECT)
ACT_BASE = "%s/galaxy/app/project/%d" % (ACT_HOST, PROJECT)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/132.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781 MiniProgramEnv/Windows "
      "WindowsWechat/WMPF XWEB/20089")

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def _noop(*a):
    pass


# ───────────────── 蜜雪侧：签名 ─────────────────

def create_str_before_sign(d):
    out = []
    for k in sorted(d.keys()):
        v = d[k]
        if v is None or v is False or v == "":
            continue
        if isinstance(v, (dict, list)):
            v = json.dumps(v, separators=(",", ":"), ensure_ascii=False)
        out.append("%s=%s" % (k, v))
    return "&".join(out)


def enhance_md5(params, ts_ms=None):
    p = dict(params)
    p.pop("sign", None)
    p["appId"] = APPID_WEIXIN
    p["t"] = ts_ms if ts_ms is not None else int(time.time() * 1000)
    p["s"] = 3
    md5hex = hashlib.md5((create_str_before_sign(p) + SALT_WEIXIN).encode("utf-8")).hexdigest()
    b = bytes.fromhex(md5hex)
    parts = []
    for i in range(4):
        v = (b[4 * i] << 24) | (b[4 * i + 1] << 16) | (b[4 * i + 2] << 8) | b[4 * i + 3]
        if v >= 0x80000000:
            v -= 0x100000000
        parts.append(str(0x7FFFFFFF if v == -0x80000000 else abs(v)))
    return md5hex + "".join(parts), p


def mx_read_token(instance, ctmp="/tmp", log=_noop, timeout=120):
    """在容器里读 accessToken（复用 wxmx.py）。

    ⚠️ 容器内路径必须**手工拼**（`/tmp/wxmx.py`）—— 不能 `os.path.join`：
    本模块跑在 Windows 上时，join 会给出 `"/tmp\\wxmx.py"`（反斜杠），容器里不存在。
    """
    remote = ctmp.rstrip("/") + "/wxmx.py"
    try:
        p = subprocess.run(["docker", "exec", "-e", "DISPLAY=:1", instance, "python3", remote],
                           capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        log("  [mx] 执行 wxmx.py 异常：%s" % e)
        return ""
    for line in ((p.stdout or "") + (p.stderr or "")).splitlines():
        if line.startswith("MX_JSON="):
            try:
                return json.loads(line[len("MX_JSON="):]).get("token", "")
            except ValueError:
                pass
    log("  [mx] 没从 wxmx.py 读到 token（rc=%s）" % p.returncode)
    return ""


def mx_call(path, params, token, cid="", method="POST"):
    sign, body = enhance_md5(params)
    body = dict(body, sign=sign)
    url = path if "://" in path else BASE + path
    headers = {"Content-Type": "application/json", "version": VER, "Access-Token": token,
               "x-ssos-cid": cid or "", "traceNo": "", "User-Agent": UA}
    if method == "GET":
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(body)
        data = None
    else:
        data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=25, context=_CTX) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


# ───────────────── 兑吧侧：会话 ─────────────────

class Dex(object):
    def __init__(self, login_url):
        self.cj = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj),
            urllib.request.HTTPSHandler(context=_CTX))
        self.op.addheaders = [("User-Agent", UA), ("Referer", ACT_URL)]
        self.login_url = login_url
        self.token = ""

    def get(self, path, retries=3):
        url = path if "://" in path else ACT_BASE + path
        last = (0, "")
        for i in range(retries):
            req = urllib.request.Request(url)
            try:
                with self.op.open(req, timeout=25) as r:
                    return r.status, r.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                return e.code, e.read().decode("utf-8", "replace")
            except Exception as e:
                last = (0, "ERR %s" % e)
                if i < retries - 1:
                    time.sleep(2.0)          # 代理偶发 SSL 握手超时，重试即可
        return last

    def login(self, log=_noop):
        st, _ = self.get(self.login_url)
        names = {c.name for c in self.cj}
        log("  [dex] 登录 http=%s cookies=%s" % (st, ",".join(sorted(names)) or "-"))
        return st == 200 and bool(names)

    def js(self, path):
        st, body = self.get(path)
        try:
            d = json.loads(body)
        except ValueError:
            return st, ""
        return st, (d.get("data") or "") if d.get("success") else ""

    def index(self):
        st, body = self.get("/luck/index.do")
        try:
            return json.loads(body)
        except ValueError:
            return {"code": "BADJSON", "raw": body[:200]}

    def draw(self, token, ticket_num=""):
        """抽奖。⚠️ 调用前必须确认 remainFreeTimes > 0，否则会扣雪王币。"""
        q = urllib.parse.urlencode({"ticketNum": ticket_num, "token": token})
        st, body = self.get("/luck/draw.do?%s" % q)
        try:
            return st, json.loads(body)
        except ValueError:
            return st, {"raw": body[:400]}


# ───────────────── Node：执行 JS-challenge ─────────────────
# 服务端下发的 JS 里，取 token 的函数名是 `eval(String.fromCharCode(...))` 拼出来的，
# **在文件里 grep 不到** → 只能「执行前后对比沙箱新增了哪个 key」来定位它。
NODE_RUNNER = r"""
const fs = require('fs'), vm = require('vm');
const files = process.argv.slice(2);
let sandbox;
function run(code){ vm.runInContext(code, sandbox.__ctx, { filename: "challenge.js" }); }
const head = { appendChild(n){ run(n.innerHTML); }, removeChild(){} };
sandbox = {
  console, setTimeout, setInterval, clearTimeout, clearInterval, Date, Math, JSON,
  String, Number, Boolean, Array, Object, RegExp, Error, parseInt, parseFloat,
  encodeURIComponent, decodeURIComponent, escape, unescape, isNaN, isFinite,
  document: {
    head, cookie: "",
    createElement(){ return { innerHTML: "", tagName: "SCRIPT" }; },
    getElementsByTagName(){ return [head]; },
    addEventListener(){},
  },
  location: { hostname: "76177-activity.dexfu.cn", protocol: "https:",
              href: "https://76177-activity.dexfu.cn/" },
  navigator: { userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MiniProgramEnv/Windows WindowsWechat/WMPF",
               platform: "Win32", language: "zh-CN" },
  screen: { width: 1280, height: 800, availWidth: 1280, availHeight: 800 },
  localStorage: { getItem(){ return null; }, setItem(){}, removeItem(){} },
  XMLHttpRequest: function(){ this.open=function(){}; this.send=function(){}; this.setRequestHeader=function(){}; },
};
sandbox.window = sandbox; sandbox.globalThis = sandbox; sandbox.self = sandbox; sandbox.top = sandbox;
sandbox.__ctx = vm.createContext(sandbox);
const before = new Set(Object.keys(sandbox));
for (const f of files) { run(fs.readFileSync(f, "utf8")); }
const added = Object.keys(sandbox).filter(k => !before.has(k));
console.error("NEWKEYS=" + (added.join(",") || "-"));
const cands = [];
if (typeof sandbox.aWlgXsMotnsZuy === "function") cands.push("aWlgXsMotnsZuy");
for (const k of added) { if (typeof sandbox[k] === "function") cands.push(k); }
for (const n of cands) {
  try {
    const v = sandbox[n]();
    if (typeof v === "string" && v.length >= 8) { console.log("TOKEN=" + v); process.exit(0); }
    console.error("call " + n + " -> " + typeof v);
  } catch (e) { console.error("call " + n + " failed: " + e.message); }
}
console.log("FAIL cands=" + (cands.join(",") || "-"));
process.exit(1);
"""


def node_token(js_list, node="node", tmpdir=None, log=_noop):
    tmpdir = tmpdir or HERE
    paths = []
    for i, js in enumerate(js_list):
        p = os.path.join(tmpdir, ".t_chal_%d.js" % i)
        with open(p, "w", encoding="utf-8") as f:
            f.write(js)
        paths.append(p)
    rp = os.path.join(tmpdir, ".t_runner.js")
    with open(rp, "w", encoding="utf-8") as f:
        f.write(NODE_RUNNER)
    try:
        p = subprocess.run([node, rp] + paths, capture_output=True, text=True, timeout=60)
    except Exception as e:
        log("  [node] 执行异常：%s" % e)
        return ""
    out = (p.stdout or "") + (p.stderr or "")
    for line in out.splitlines():
        if line.startswith("TOKEN="):
            return line[len("TOKEN="):]
    log("  [node] 没拿到 token：%s" % out.strip()[:300])
    return ""


# ───────────────── 统一入口 ─────────────────

def get_session(instance, ctmp="/tmp", log=_noop):
    """① → ② → ③：蜜雪 token → 免密登录 URL → 兑吧会话。"""
    mx = mx_read_token(instance, ctmp, log)
    if not mx:
        return None, "NOIDENT", "读不到蜜雪 accessToken（小程序开着吗？wxmx.py 投递了吗？）"
    log("  [mx] token=%s…（%d 字符）" % (mx[:20], len(mx)))
    try:
        r = mx_call("/v1/duiba/getLoginUrl", {"dbredirect": ACT_URL}, mx, "", "GET")
    except Exception as e:
        return None, "NETFAIL", "getLoginUrl 失败：%s" % e
    login_url = (r.get("data") or {}).get("loginUrl")
    if not login_url:
        return None, "NOLOGIN", "getLoginUrl 没返回 loginUrl：%s" % json.dumps(r, ensure_ascii=False)[:200]
    uid = urllib.parse.parse_qs(urllib.parse.urlparse(login_url).query).get("uid", [""])[0]
    log("  [mx] loginUrl 拿到（uid=%s）" % uid)
    dex = Dex(login_url)
    if not dex.login(log):
        return None, "NOLOGIN", "兑吧免密登录失败"
    return dex, "", ""


def run(mode="draw", instance="", ctmp="/tmp", node=None, log=_noop, dump_dir=None):
    """统一入口。返回 (ok, code, msg)。

    mode: "probe" 只查状态；"draw" 查完再抽（**仅在免费次数 > 0 时**）。
    """
    if not instance:
        return False, "NOINSTANCE", "未给微信实例容器名（WOC_INSTANCE）"
    dex, code, msg = get_session(instance, ctmp, log)
    if dex is None:
        return False, code, msg

    # ④ 查状态（不改任何状态）
    idx = dex.index()
    if idx.get("raw") is not None:
        return False, "NETFAIL", "luck/index.do 异常：%s" % json.dumps(idx, ensure_ascii=False)[:200]
    if str(idx.get("code")) != "000000":
        return False, str(idx.get("code")), "luck/index.do 回 %s（%s）" % (
            idx.get("code"), idx.get("desc") or idx.get("message"))
    d = idx.get("data") or {}
    remain = d.get("remainFreeTimes") or 0
    log("  [info] 剩余免费=%s 已抽=%s 雪王币=%s 单次消耗=%s notLogin=%s"
        % (remain, d.get("hadPlayTime"), d.get("credits"),
           d.get("preConsumeCredits"), d.get("notLogin")))
    if d.get("notLogin"):
        return False, "NOTOKEN", "兑吧侧判定未登录（cookie 失效？）"

    if mode == "probe":
        return True, "200", "查询成功：今天还剩 %s 次免费抽奖" % remain

    # ⚠️ 关键保护：剩 0 次时**绝不调 draw** —— 那会扣 20 雪王币
    if remain <= 0:
        return True, "415", ("今日免费次数已用完（服务端 remainFreeTimes=0）—— "
                             "继续抽会扣 %s 雪王币，已跳过" % d.get("preConsumeCredits"))

    # ⑤ token
    js_list = []
    for i in range(3):
        _, js1 = dex.js("/getTokenKey.query?projectId=%d" % PROJECT)
        _, js2 = dex.js("/getToken.query?projectId=%d" % PROJECT)
        if js1 and js2:
            js_list = [js1, js2]
            break
        log("  [chal] 第 %d 次拉取失败（%d/%d 字符），重试" % (i + 1, len(js1), len(js2)))
        time.sleep(1.5)
    if not js_list:
        return False, "NOCHAL", "拿不到 JS-challenge（getTokenKey/getToken.query）"
    log("  [chal] JS 长度 %s" % [len(x) for x in js_list])

    if dump_dir:
        os.makedirs(dump_dir, exist_ok=True)
        for i, js in enumerate(js_list):
            with open(os.path.join(dump_dir, "chal_%d.js" % i), "w", encoding="utf-8") as f:
                f.write(js)
        log("  已写出两段 JS 到 %s" % dump_dir)

    token = node_token(js_list, node or os.environ.get("WXSIGN_NODE", "node"), HERE, log)
    if not token:
        return False, "NOTOKEN2", "Node 没能从 JS-challenge 里算出 token"
    log("  [chal] token=%s…（%d 字符）" % (token[:16], len(token)))

    # ⑥ 抽奖
    st, res = dex.draw(token, "")
    ok = bool(res.get("success")) and str(res.get("code")) == "000000"
    prize = ((res.get("data") or {}).get("sendPrizeResult") or {})
    log("  [draw] http=%s code=%s prize=%s"
        % (st, res.get("code"), prize.get("optionName") or res.get("desc")))
    if not ok:
        return False, str(res.get("code")), "抽奖失败：%s" % (res.get("desc") or res.get("message"))
    left = (res.get("data") or {}).get("remainFreeTimes")
    return True, "200", "抽奖成功：%s（剩余免费 %s）" % (prize.get("optionName") or "?", left)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--draw", action="store_true")
    ap.add_argument("--dump-js", metavar="DIR")
    ap.add_argument("--instance", default=os.environ.get("WOC_INSTANCE", ""))
    ap.add_argument("--ctmp", default=os.environ.get("WXSIGN_CTMP", "/tmp"))
    ap.add_argument("--node", default=os.environ.get("WXSIGN_NODE", "node"))
    a = ap.parse_args()
    mode = "probe" if a.probe and not a.draw else "draw"
    ok, code, msg = run(mode=mode, instance=a.instance, ctmp=a.ctmp, node=a.node,
                        log=lambda *x: print(*x), dump_dir=a.dump_js)
    print("RESULT ok=%s code=%s msg=%s" % (ok, code, msg))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
