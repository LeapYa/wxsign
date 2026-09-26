#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断工具：授权层到底在哪个渲染面 —— 排查「wxqm_auth.py 找不到授权层」时用。

## 背景（这是本项目踩过最深的一个坑，务必读完）

企迈的授权层（「欢迎加入<品牌> / 手机号一键登录」）是**普通 view 组件**，
源码写得明明白白：
    `onAuthorization(){ this.selectComponent("#authorization").show() }`
    （`pluginMarketing/components/authorization-4e03c673`）
渲染出来是 `<wx-std-authorization id="authorization">`。

但 `wxdom.scan()` 一开始**扫不到它**，我因此错误地断定「授权层不在渲染层 DOM」，
还据此写了个 `is_orange()` 按颜色找按钮 —— 被用户否决（企迈几百家商户，主题跟活动走）。

**真相**：同一个逻辑层页面可以有**多个渲染面**。呷哺签到页弹授权层时实测：

    ctx=6   302 节点 / 161 个 wx-* 标签 / **没有授权层**   ← 底层页面（「我的」页）
    ctx=9   221 节点 / 152 个 wx-* 标签 / **有授权层**     ← `#authorization` 在这儿
    ctx=10   11 节点 /   0 个 wx-* 标签 / 没有              ← 空壳

两者 url 相同、title 都是 `Page-Frame`，**唯一区别是里面的内容**。
而 `wxdom.find_dom_ctx()` 的判据是「谁元素多选谁」→ **必然选到 ctx=6**。
**教训：不是「找不到」，是「找错了地方」。**

所以正式解法是 `wxdom.find_ctx_with(["#authorization"])` ——
**先说要找什么，再看哪个 ctx 有**（`wxqm_auth.find_auth_ctx` 就是这么做的）。

## 用法（容器内）

    DISPLAY=:1 python3 diagnostics_auth_ctx.py            # 列全 ctx + 标出哪个有授权层
    DISPLAY=:1 python3 diagnostics_auth_ctx.py --tree     # 再把授权层结构倒出来

## 另一条记录：逻辑层查不出来（已验证，不用再试）

曾经想用逻辑层绕开这个问题，实测**全部走不通**：
    · 页面 data 里 46 个字段，没有一个跟授权层相关
    · `wx.createSelectorQuery()` 对 14 个选择器**全部返回空**
    · `Runtime.executionContextCreated` 事件这个代理**根本不发**，
      所以要靠**盲撒 ctx 1..80** 找逻辑层（就是 ctx=3，
      凭据：`wx.getAccountInfoSync()` 拿得到 appId、`getCurrentPages()` 拿得到路由）
"""
import argparse
import json
import os
import sys
import time

# 既能在仓库里直接跑（同级目录就是 wxdom/wxcdp），也能在容器里跑（拷到 /tmp）。
_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, ".."), "/tmp"):
    if os.path.isfile(os.path.join(_p, "wxcdp.py")):
        sys.path.insert(0, _p)
        break
try:
    import wxcdp
    import wxdom
except ImportError:
    sys.exit("找不到 wxcdp.py / wxdom.py —— 把它们拷到同一个目录（容器里是 /tmp）再跑")

PROBE_JS = r"""(function(){try{
  var all=document.querySelectorAll('*');
  var wx=0;
  for(var i=0;i<all.length;i++){if(all[i].tagName.indexOf('WX-')===0) wx++;}
  var body=(document.body&&document.body.innerHTML)||'';
  return JSON.stringify({
    n:all.length, wx:wx,
    auth:!!document.querySelector('#authorization'),
    marks:['欢迎加入','手机号一键登录','暂时跳过','请阅读并同意'].filter(function(w){
      return body.indexOf(w)>=0;}),
    t:(document.title||'').slice(0,16),
    u:(location.href||'').slice(-30)
  });
}catch(e){return 'ERR:'+String(e).slice(0,50)}})()"""

TREE_JS = None  # 见 dump 部分


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", action="store_true", help="倒出授权层结构（需授权层已弹出）")
    ap.add_argument("--limit", type=int, default=30, help="扫到第几个 ctx（默认 30）")
    a = ap.parse_args()

    ws = wxcdp.WS(timeout=25)
    ws.send({"id": 1, "method": "Runtime.enable", "params": {}})
    time.sleep(0.3)

    print("=== 各渲染面 ===")
    base = 4000
    for c in range(1, a.limit + 1):
        ws.send({"id": base + c, "method": "Runtime.evaluate",
                 "params": {"expression": PROBE_JS, "returnByValue": True, "contextId": c}})
    info = {}
    t0 = time.time()
    while time.time() - t0 < 14:
        try:
            m = json.loads(ws.recv_msg())
        except Exception:
            break
        mid = m.get("id")
        if not (isinstance(mid, int) and base < mid <= base + a.limit):
            continue
        v = ((m.get("result") or {}).get("result") or {}).get("value")
        if v and not str(v).startswith("ERR"):
            info[mid - base] = v

    auth_ctx = 0
    for c, v in sorted(info.items()):
        try:
            d = json.loads(v)
        except ValueError:
            continue
        if not d.get("n"):
            continue
        tag = ""
        if d.get("auth") or d.get("marks"):
            tag = "  ⭐⭐ 授权层在这儿（标记：%s）" % ",".join(d.get("marks") or [])
            auth_ctx = c
        elif d.get("wx"):
            tag = "  （页面渲染面）"
        print("ctx=%-3d 节点=%-5d wx标签=%-5d title=%-16s %s%s" % (
            c, d["n"], d["wx"], d.get("t", ""), d.get("u", ""), tag))

    if not auth_ctx:
        print("\n✗ 没找到授权层 —— 它弹出来了吗？（可先跑 `wxqm_auth.py --dry-run` 弹它）")
        return 1
    print("\n✅ 授权层在 ctx=%d。`wxdom.find_ctx_with(['#authorization'])` 应返回这个。" % auth_ctx)

    # 自检：正式的定位 API 是否认同
    hits = wxdom.find_ctx_with(ws, ["#authorization"])
    print("   自检 find_ctx_with → %s" % hits)
    d = wxdom.rect_of(ws, auth_ctx, ("手机号一键登录", "一键登录"))
    if d and d["items"]:
        b = d["items"][0]
        print("   自检主按钮   → %s「%s」(%d,%d) %dx%d" % (
            b["tag"], b["text"][:12], b["cx"], b["cy"], b["w"], b["h"]))

    if a.tree:
        print("\n=== 授权层结构 ===")
        import importlib.util
        spec = importlib.util.spec_from_file_location("dump_auth_tree", "/tmp/dump_auth_tree.py")
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            orig = sys.argv
            sys.argv = ["dump_auth_tree"]
            mod.CTX = auth_ctx
            mod.main()
            sys.argv = orig
        else:
            print("   （dump_auth_tree.py 不在 /tmp）")
    ws.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
