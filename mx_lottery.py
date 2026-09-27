#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""蜜雪「雪王币抽奖」（兑吧 duiba 大转盘）—— 纯 HTTP 复刻，含 JS-challenge token。

## 完整链路（全部来自解包/抓包，非猜测）
```
① 蜜雪 accessToken（容器内 CDP 读 getApp().globalData.accessToken）
② GET  https://mxsa.mxbc.net/api/v1/duiba/getLoginUrl?dbredirect=<活动URL>
     → data.loginUrl = <活动域>/autoLogin/autologin?…&uid=…&sign=…   （免密登录）
③ 访问 loginUrl → 拿 dexfu 会话 cookie（wdata12 / tokenId）
④ GET  <活动>/galaxy/app/project/<id>/luck/index.do    查状态（**不需要 token**）
     → remainFreeTimes / hadPlayTime / credits / preConsumeCredits
⑤ token：兑吧的 JS-challenge
     GET  <活动>/galaxy/app/project/<id>/getTokenKey.query?projectId=<id>  → JS1
     GET  <活动>/galaxy/app/project/<id>/getToken.query?projectId=<id>     → JS2
     原码（vendors.js）：
         p(js) = <script> 标签注入执行
         token = window.aWlgXsMotnsZuy()      ← JS2 里定义的全局函数
⑥ POST <活动>/galaxy/app/project/<id>/luck/draw.do   params={ticketNum, token}
```

## 签名（蜜雪侧，utils/enhanceMD5.js）
    params 补 appId / t(ms) / s=3
    sign = md5( key 升序 "k=v&k=v" + salt_weixin ) + 「4 组有符号大端 int 绝对值」拼接

用法：
    python3 mx_lottery.py --probe          # 只查状态（④，不触发抽奖）
    python3 mx_lottery.py --draw           # 走完 ⑤ + ⑥ 真抽一次
    python3 mx_lottery.py --dump-js <dir>  # 只导出两段 JS（给 node 用）
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
sys.path.insert(0, HERE)

BASE = "https://mxsa.mxbc.net/api"
VER = "2.8.58"
SALT_WEIXIN = "0d787c102fe2f7b4279af8925819d5fd"
APPID_WEIXIN = "d82be6bbc1da11eb9dd000163e122ecb"
APPID = "wx7696c66d2245d107"

ACT_HOST = "https://76177-activity.dexfu.cn"
PROJECT = 2924
ACT_URL = "%s/galaxy/app/project/%d/index.html" % (ACT_HOST, PROJECT)
ACT_BASE = "%s/galaxy/app/project/%d" % (ACT_HOST, PROJECT)

INSTANCE = os.environ.get("WOC_INSTANCE", "woc-wx-2ada0225ca")
CTMP = os.environ.get("WXSIGN_CTMP", "/tmp")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/132.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781 MiniProgramEnv/Windows "
      "WindowsWechat/WMPF XWEB/20089")

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def log(*a):
    print(*a)


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


def mx_read_token():
    """通过 docker exec 在容器里读 accessToken（复用 wxmx.py）。

    ⚠️ 容器内路径必须**手工拼**（`/tmp/wxmx.py`）—— 别用 `os.path.join`：
    本脚本跑在 Windows 上，join 会给出 `"/tmp\\wxmx.py"`（反斜杠），容器里不存在。
    """
    remote = CTMP.rstrip("/") + "/wxmx.py"
    try:
        p = subprocess.run(["docker", "exec", "-e", "DISPLAY=:1", INSTANCE, "python3", remote],
                           capture_output=True, text=True, timeout=120)
    except Exception as e:
        log("  [mx] 执行 wxmx 异常：%s" % e)
        return ""
    for line in ((p.stdout or "") + (p.stderr or "")).splitlines():
        if line.startswith("MX_JSON="):
            try:
                return json.loads(line[len("MX_JSON="):]).get("token", "")
            except ValueError:
                pass
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
    """兑吧侧会话（cookie jar）+ 请求。"""

    def __init__(self, login_url):
        self.cj = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj),
            urllib.request.HTTPSHandler(context=_CTX))
        self.op.addheaders = [("User-Agent", UA), ("Referer", ACT_URL)]
        self.login_url = login_url
        self.token = ""

    def get(self, path, **kw):
        url = path if "://" in path else ACT_BASE + path
        req = urllib.request.Request(url, headers=kw.pop("headers", {}))
        try:
            with self.op.open(req, timeout=25) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception as e:
            return 0, "ERR %s" % e

    def login(self):
        """走免密登录 URL，建立会话。"""
        st, body = self.get(self.login_url)
        names = {c.name for c in self.cj}
        log("  [dex] 登录 http=%s cookies=%s" % (st, ",".join(sorted(names)) or "-"))
        return "wdata12" in names or "wdata3" in names or st == 200

    def js(self, path):
        st, body = self.get(path)
        try:
            d = json.loads(body)
        except ValueError:
            return st, ""
        if not d.get("success"):
            return st, ""
        return st, d.get("data") or ""

    def draw(self, token, ticket_num=""):
        """抽奖。token 放进 params（原码 `t.token = i`）。"""
        q = urllib.parse.urlencode({"ticketNum": ticket_num, "token": token})
        st, body = self.get("/luck/draw.do?%s" % q)
        try:
            return st, json.loads(body)
        except ValueError:
            return st, {"raw": body[:400]}


# ───────────────── Node：执行 JS-challenge ─────────────────

NODE_RUNNER = r"""
const fs = require('fs'), vm = require('vm');
const files = process.argv.slice(2);
// 浏览器环境 mock：原码把 JS 塞进 <script> 注入 document.head 执行
const store = {};
const head = { appendChild(n){ run(n.innerHTML); }, removeChild(){} };
const documentMock = {
  head, createElement(){ return { innerHTML: "", tagName: "SCRIPT" }; },
  getElementsByTagName(){ return [head]; },
  addEventListener(){}, cookie: "",
};
const sandbox = {
  console, setTimeout, setInterval, clearTimeout, clearInterval, Date, Math, JSON,
  String, Number, Boolean, Array, Object, RegExp, Error, parseInt, parseFloat,
  encodeURIComponent, decodeURIComponent, escape, unescape, isNaN, isFinite,
  document: documentMock,
  location: { hostname: "76177-activity.dexfu.cn", href: "https://76177-activity.dexfu.cn/", protocol: "https:" },
  navigator: { userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MiniProgramEnv/Windows WindowsWechat/WMPF", platform: "Win32", language: "zh-CN" },
  screen: { width: 1280, height: 800, availWidth: 1280, availHeight: 800 },
  localStorage: { getItem(){return null;}, setItem(){}, removeItem(){} },
  XMLHttpRequest: function(){ this.open=function(){}; this.send=function(){}; this.setRequestHeader=function(){}; },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.self = sandbox;
sandbox.top = sandbox;
const ctx = vm.createContext(sandbox);
function run(code){ vm.runInContext(code, ctx, { filename: "challenge.js" }); }
// ⚠️ 先在**执行前**记录已知 key —— 服务端 JS 会 eval 出代码并定义**取 token 的全局函数**，
//    那个函数名藏在 `String.fromCharCode(...)` 拼出的字符串里（grep 文件搜不到），
//    所以只能靠「执行后新增了哪些全局函数」来定位它。
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


def node_token(js_list, tmpdir="."):
    """把若干段 JS 交给 node 执行，返回 token。"""
    paths = []
    for i, js in enumerate(js_list):
        p = os.path.join(tmpdir, ".t_chal_%d.js" % i)
        with open(p, "w", encoding="utf-8") as f:
            f.write(js)
        paths.append(p)
    rp = os.path.join(tmpdir, ".t_runner.js")
    with open(rp, "w", encoding="utf-8") as f:
        f.write(NODE_RUNNER)
    node = os.environ.get("WXSIGN_NODE", "node")
    try:
        p = subprocess.run([node, rp] + paths, capture_output=True, text=True, timeout=60)
    except Exception as e:
        log("  [node] 执行异常：%s" % e)
        return ""
    out = (p.stdout or "") + (p.stderr or "")
    for line in out.splitlines():
        if line.startswith("TOKEN="):
            return line[len("TOKEN="):]
    log("  [node] 没拿到 token：%s" % out.strip()[:400])
    return ""


# ───────────────── 主流程 ─────────────────

def get_session(verbose=True):
    """① → ② → ③：蜜雪 token → 免密登录 URL → 兑吧会话。"""
    mx = mx_read_token()
    if not mx:
        log("!! 读不到蜜雪 accessToken（小程序开着吗？wxmx.py 投递了吗？）")
        return None
    if verbose:
        log("  [mx] token=%s…（%d 字符）" % (mx[:20], len(mx)))
    r = mx_call("/v1/duiba/getLoginUrl", {"dbredirect": ACT_URL}, mx, "", "GET")
    d = r.get("data") or {}
    login_url = d.get("loginUrl")
    if not login_url:
        log("!! getLoginUrl 失败：%s" % json.dumps(r, ensure_ascii=False)[:300])
        return None
    if verbose:
        log("  [mx] loginUrl 拿到（uid=%s）" % (urllib.parse.parse_qs(
            urllib.parse.urlparse(login_url).query).get("uid", [""])[0]))
    dex = Dex(login_url)
    if not dex.login():
        log("!! 兑吧登录失败")
        return None
    return dex


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="只查状态，不抽奖")
    ap.add_argument("--draw", action="store_true", help="真抽一次")
    ap.add_argument("--dump-js", metavar="DIR", help="导出两段 challenge JS")
    ap.add_argument("--node", default="node")
    ap.add_argument("--max-retry", type=int, default=3)
    a = ap.parse_args()

    dex = get_session()
    if dex is None:
        return 1

    # ④ 查状态
    st, body = dex.get("/luck/index.do")
    try:
        idx = json.loads(body)
    except ValueError:
        log("!! luck/index.do 返回非 JSON：%s" % body[:300])
        return 1
    d = idx.get("data") or {}
    log("  [info] code=%s 剩余免费=%s 已抽=%s 雪王币=%s 单次消耗=%s notLogin=%s"
        % (idx.get("code"), d.get("remainFreeTimes"), d.get("hadPlayTime"),
           d.get("credits"), d.get("preConsumeCredits"), d.get("notLogin")))

    if a.probe and not a.draw and not a.dump_js:
        return 0

    # ⑤ token
    need_js = bool(a.dump_js) or a.draw
    if not need_js:
        return 0
    js_list = []
    for i in range(a.max_retry):
        st1, js1 = dex.js("/getTokenKey.query?projectId=%d" % PROJECT)
        st2, js2 = dex.js("/getToken.query?projectId=%d" % PROJECT)
        if js1 and js2:
            js_list = [js1, js2]
            break
        log("  [chal] 第 %d 次拉取失败（key=%s token=%s），重试" % (i + 1, len(js1), len(js2)))
        time.sleep(1.5)
    if not js_list:
        log("!! 拿不到 challenge JS")
        return 1
    log("  [chal] JS 长度 %s" % [len(x) for x in js_list])

    if a.dump_js:
        os.makedirs(a.dump_js, exist_ok=True)
        for i, js in enumerate(js_list):
            p = os.path.join(a.dump_js, "chal_%d.js" % i)
            with open(p, "w", encoding="utf-8") as f:
                f.write(js)
            log("  写出 %s（%d 字符）" % (p, len(js)))
        return 0

    token = node_token(js_list, tmpdir=os.path.dirname(os.path.abspath(__file__)))
    if not token:
        return 1
    log("  [chal] token=%s…（%d 字符）" % (token[:24], len(token)))
    dex.token = token

    # ⑥ 抽奖
    if a.draw:
        st, res = dex.draw(token, "")
        log("  [draw] http=%s %s" % (st, json.dumps(res, ensure_ascii=False)[:800]))
        st2, body2 = dex.get("/luck/index.do")
        try:
            d2 = json.loads(body2).get("data") or {}
            log("  [info] 抽后：剩余免费=%s 已抽=%s 雪王币=%s"
                % (d2.get("remainFreeTimes"), d2.get("hadPlayTime"), d2.get("credits")))
        except ValueError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
