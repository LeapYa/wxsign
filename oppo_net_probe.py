# -*- coding: utf-8 -*-
"""临时探针：不依赖小程序面板，走「全局搜索 → 搜一搜结果页」看 OPPO 商城有没有结果行。
只读 + 截图，不点击任何结果行。"""
import sys
sys.path.insert(0, "/tmp")
import wxwin
import wxopen as R

W, H = wxwin.screen()
R.KEYWORD = "OPPO商城"

R.close_anon_modal(W, H)
if not R.ensure_chat_tab(W, H):
    print("PROBE FAIL: 切不到微信标签")
    sys.exit(1)
box = R.find_search_box(W, H)
if not box:
    print("PROBE FAIL: 找不到全局搜索框")
    sys.exit(1)
if box[3] > R.PLACEHOLDER_MAX:
    print("PROBE FAIL: 像是局部搜索框 span=%d" % box[3])
    sys.exit(1)
if not R.do_search(W, H, box, goto_net=True):
    print("PROBE FAIL: 跳搜一搜结果页失败")
    sys.exit(1)

import time
time.sleep(2.0)
buf = R.grab(W, H)
rows = R.find_rows(buf, W, H)
print("PROBE rows=%d %s" % (len(rows), rows[:12]))
R.png(W, H, "netsearch.png")
print("PROBE shot saved")
