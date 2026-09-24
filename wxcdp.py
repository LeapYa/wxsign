#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""最小 WebSocket + CDP 客户端（纯标准库），用来读小程序逻辑层的 appId。

为什么不用 cdp_eval.js：那个在 woc-hook 容器里跑，而屏幕自动化必须在微信实例容器里跑
（X11 socket 不跨容器）。但 hook 容器与实例**共享网络命名空间**，所以实例容器里
连 127.0.0.1:62000 就是那个 CDP 代理。于是把「读 appId」内联进驱动脚本，
每一步都拿得到准确身份，不用事后靠包目录 mtime 猜。

appId 是与界面形态无关的硬判据：`wx.getAccountInfoSync().miniProgram.appId`。
"""
import base64
import hashlib
import json
import os
import socket
import struct
import time

PORT = int(os.environ.get("CDP_PORT", "62000"))
PROBE = "(function(){try{return wx.getAccountInfoSync().miniProgram.appId}catch(e){return ''}})()"


class WS(object):
    """够用的 WebSocket 客户端：握手 + 发文本帧（掩码）+ 收帧（含分片/心跳）。"""

    def __init__(self, host="127.0.0.1", port=PORT, timeout=8):
        self.s = socket.create_connection((host, port), timeout=timeout)
        self.s.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = ("GET / HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n"
               "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
               "Sec-WebSocket-Version: 13\r\n\r\n" % (host, port, key))
        self.s.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.s.recv(4096)
            if not chunk:
                raise RuntimeError("握手失败：连接被关闭")
            buf += chunk
        if b"101" not in buf.split(b"\r\n")[0]:
            raise RuntimeError("握手失败：%r" % buf.split(b"\r\n")[0])
        want = base64.b64encode(hashlib.sha1(
            (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        if want.lower().encode() not in buf.lower():
            raise RuntimeError("握手校验失败（Sec-WebSocket-Accept 不匹配）")
        self.buf = buf.split(b"\r\n\r\n", 1)[1]
        self.pending = b""

    # ── 收 ──
    def _read_exact(self, n):
        while len(self.pending) < n:
            chunk = self.s.recv(65536)
            if not chunk:
                raise RuntimeError("连接关闭")
            self.pending += chunk
        out, self.pending = self.pending[:n], self.pending[n:]
        return out

    def recv_msg(self):
        """返回一条完整文本消息（str）；超时抛 socket.timeout。"""
        data = b""
        while True:
            b1, b2 = self._read_exact(2)
            fin, opcode = b1 & 0x80, b1 & 0x0F
            ln = b2 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._read_exact(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._read_exact(8))[0]
            payload = self._read_exact(ln) if ln else b""
            if opcode == 9:                       # ping → pong
                self._send_frame(10, payload)
                continue
            if opcode == 10:                      # pong
                continue
            if opcode == 8:                       # close
                raise RuntimeError("服务端关闭连接")
            if opcode in (1, 2):
                data = payload
            elif opcode == 0:
                data += payload
            if fin and opcode in (0, 1, 2):
                return data.decode("utf-8", "replace")

    # ── 发 ──
    def _send_frame(self, opcode, payload):
        head = bytes([0x80 | opcode])
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        masked = bytes(payload[i] ^ mask[i % 4] for i in range(n))
        self.s.sendall(head + mask + masked)

    def send(self, obj):
        self._send_frame(1, json.dumps(obj).encode())

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass


def probe_appids(wait=6.0):
    """返回当前打开的小程序 appId 列表（可能同时开着多个）。连不上返回 None。"""
    try:
        ws = WS()
    except Exception as e:
        print("[cdp] 连不上 %d: %s" % (PORT, e))
        return None
    found = []
    try:
        ws.send({"id": 1, "method": "Runtime.enable", "params": {}})
        time.sleep(0.4)
        for c in range(1, 81):
            ws.send({"id": 1000 + c, "method": "Runtime.evaluate",
                     "params": {"expression": PROBE, "returnByValue": True, "contextId": c}})
        t0 = time.time()
        while time.time() - t0 < wait:
            try:
                m = json.loads(ws.recv_msg())
            except socket.timeout:
                break
            except Exception:
                break
            mid = m.get("id")
            if isinstance(mid, int) and 1000 < mid < 2000:
                r = (m.get("result") or {}).get("result") or {}
                v = r.get("value")
                if isinstance(v, str) and v and v not in found:
                    found.append(v)
    finally:
        ws.close()
    return found


if __name__ == "__main__":
    print("APPID=%s" % ",".join(probe_appids() or []))
