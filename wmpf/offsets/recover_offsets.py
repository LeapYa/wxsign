"""从 WeChatAppEx 二进制自动恢复 WMPF hook 偏移配置（离线静态分析，不需要 IDA / 不需要运行微信）。

锚点规则对齐上游 WMPFDebugger 的自动化实现（frida/autodetect/win32.js），只是把
frida/IDA 换成离线 ELF 解析，并补上 Linux 两份二进制实测出来的差异。

已在两个版本上机器判卷通过：
  · WMPF 2.5.6.25665（微信 Linux 4.1.13.23 / 4.1.13.9）——5 个字段与上游 addresses.25665.json 全一致
  · WMPF 2.2.4.14978（微信 Linux 4.1.1.8）      ——3 个字段与上游 addresses.14978.json 全一致

规则：
  Version            `wmpf_release/<tag>_<x.y.z>` 取出 x.y.z，再找 `x.y.z.<build>`
  LoadStartHookOffset 新版：同时引用 `applet_index_container.cc` 与 `AppletIndexContainer::OnLoadStart(bool`
                          的**唯一**函数；旧版：引用 `[Perf] AppletIndexContainer::OnLoadStart` 的函数
  SceneOffsets       守卫函数 = 引用 `create webview devtools failed.` 的函数；
                     从里面的 `cmp [reg+X], 1101` 出发做寄存器反向切片，取 6 层字段偏移
  CDPFilterHookOffset 新版：同时引用 `SendToClientFilter` 与 `devtools_message_filter_applet_webview.cc`
                          的唯一函数；旧版：该 owner 调用的**第一个**函数
  CastToJsonHookOffset 同时引用 `CastToJson` 与 `devtools_message_filter.cc` 的唯一函数

依赖: capstone, numpy
用法: python recover_offsets.py <WeChatAppEx 路径> [--out 输出.json] [--cdp-rule auto|self|callee]
"""
import argparse
import bisect
import json
import re
import struct
import sys

import capstone
import numpy as np

SCENE_MAGIC = 0x44D                    # 1101：官方文档提到的场景判定值
ARG_REGS = {"rdi", "rsi", "rdx", "rcx", "r8", "r9"}

# 锚点字符串（照搬上游 buildStringTargets）
A_LOAD_FILE = b"applet_index_container.cc"
A_LOAD_NAME = b"AppletIndexContainer::OnLoadStart(bool"
A_LOAD_PERF = b"[Perf] AppletIndexContainer::OnLoadStart"      # 旧版专有
A_FILTER_NAME = b"SendToClientFilter"
A_FILTER_FILE = b"devtools_message_filter_applet_webview.cc"
A_CAST_NAME = b"CastToJson"
A_CAST_FILE = b"devtools_message_filter.cc"
A_GUARD = b"create webview devtools failed."                   # 守卫函数内的日志串
A_RELEASE = b"wmpf_release/"


# ────────────────────────── ELF ──────────────────────────
class Elf:
    def __init__(self, path):
        self.buf = open(path, "rb").read()
        if self.buf[:4] != b"\x7fELF":
            raise ValueError("不是 ELF 文件")
        if self.buf[4] != 2:
            raise ValueError("只支持 ELF64")
        e_shoff, = struct.unpack_from("<Q", self.buf, 40)
        e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", self.buf, 58)
        secs = []
        for i in range(e_shnum):
            sh = struct.unpack_from("<IIQQQQIIQQ", self.buf, e_shoff + i * e_shentsize)
            secs.append({"name_off": sh[0], "addr": sh[3], "offset": sh[4], "size": sh[5]})
        st = secs[e_shstrndx]
        strtab = self.buf[st["offset"]:st["offset"] + st["size"]]
        for s in secs:
            s["name"] = strtab[s["name_off"]:strtab.index(b"\x00", s["name_off"])].decode("ascii", "replace")
        self.secs = secs
        self.text = self.by_name(".text")
        self.delta = self.text["addr"] - self.text["offset"]
        self.starts = self._func_starts()
        self._start_set = set(int(x) for x in self.starts)

    def by_name(self, n):
        for s in self.secs:
            if s["name"] == n:
                return s
        return None

    def _func_starts(self):
        """解析 .eh_frame_hdr，得到排序的函数起始 vaddr 表"""
        hdr = self.by_name(".eh_frame_hdr")
        if hdr is None:
            raise ValueError("没有 .eh_frame_hdr 节，无法切函数")
        b = self.buf[hdr["offset"]:hdr["offset"] + hdr["size"]]
        if b[1] != 0x1B or b[2] != 0x03 or b[3] != 0x3B:
            raise ValueError(f".eh_frame_hdr 编码不支持: {b[1]:#x}/{b[2]:#x}/{b[3]:#x}")
        fde_count, = struct.unpack_from("<I", b, 8)
        # 每项 8 字节 = 两个 int32（initial_location, fde_address），共 fde_count*2 个 int32
        raw = np.frombuffer(self.buf, dtype="<i4", count=fde_count * 2,
                            offset=hdr["offset"] + 12)
        return np.sort((raw[0::2].astype(np.int64) + hdr["addr"]))

    def vaddr2off(self, v):
        for s in self.secs:
            if s["size"] and s["addr"] <= v < s["addr"] + s["size"]:
                return s["offset"] + (v - s["addr"])
        return None

    def off2vaddr(self, o):
        for s in self.secs:
            if s["size"] and s["offset"] <= o < s["offset"] + s["size"]:
                return s["addr"] + (o - s["offset"])
        return None

    def cstr_start(self, pos):
        """回溯到 C 字符串起点（编译器会把源文件路径做成更长串的后缀）"""
        st = self.buf.rfind(b"\x00", max(0, pos - 4096), pos)
        return st + 1 if st >= 0 else pos

    def func_containing(self, v):
        i = bisect.bisect_right(self.starts.tolist(), v) - 1
        return int(self.starts[i]) if i >= 0 else None

    def func_end(self, v):
        i = bisect.bisect_right(self.starts.tolist(), v)
        return int(self.starts[i]) if i < len(self.starts) else self.text["addr"] + self.text["size"]

    def is_func_start(self, v):
        return int(v) in self._start_set


# ────────────────────────── 交叉引用 ──────────────────────────
def xrefs_many(elf, pats, chunk=8_000_000):
    """一次扫 .text，同时求多个锚点串的 RIP 相对引用位置。

    返回 {锚点串: [命中位置(文件偏移), ...]}，位置是 disp32 那一处。
    """
    vaddrs = {}
    for p in pats:
        pos = elf.buf.find(p)
        if pos >= 0:
            v = elf.off2vaddr(elf.cstr_start(pos))
            if v is not None:
                vaddrs[p] = v
    want = sorted(set(vaddrs.values()))
    hits = {v: [] for v in want}
    text, delta = elf.text, elf.delta
    for sh in range(4):
        n = (text["size"] - sh) // 4
        for s in range(0, n, chunk):
            e = min(s + chunk, n)
            arr = np.frombuffer(elf.buf, dtype="<i4", count=e - s,
                                offset=text["offset"] + sh + s * 4).astype(np.int64)
            p = text["offset"] + sh + np.arange(s, e, dtype=np.int64) * 4
            tgt = p + 4 + delta + arr
            for v in want:
                m = tgt == v
                if m.any():
                    for pf in p[m]:
                        if (elf.buf[pf - 1] & 0xC7) == 0x05:      # RIP 相对寻址的 modrm
                            hits[v].append(int(pf))
    out = {}
    for p, v in vaddrs.items():
        fs = []
        for pos in sorted(hits[v]):
            f = elf.func_containing(elf.off2vaddr(pos))
            if f is not None and f not in fs:
                fs.append(f)
        out[p] = fs
    return out


def find_xrefs(elf, target_vaddr, chunk=8_000_000):
    """求单个目标 vaddr 的全部 RIP 相对引用位置（向后兼容用）"""
    text, delta = elf.text, elf.delta
    hits = []
    for sh in range(4):
        n = (text["size"] - sh) // 4
        for s in range(0, n, chunk):
            e = min(s + chunk, n)
            arr = np.frombuffer(elf.buf, dtype="<i4", count=e - s,
                                offset=text["offset"] + sh + s * 4).astype(np.int64)
            p = text["offset"] + sh + np.arange(s, e, dtype=np.int64) * 4
            m = (p + 4 + delta + arr) == target_vaddr
            for pf in p[m]:
                if (elf.buf[pf - 1] & 0xC7) == 0x05:
                    hits.append(int(pf))
    return sorted(hits)


def anchor_func(elf, pattern, unique=False):
    """字符串锚点 -> 交叉引用 -> 所属函数（向后兼容用）"""
    pos = elf.buf.find(pattern)
    if pos < 0:
        return None, 0, "字符串不存在"
    v = elf.off2vaddr(elf.cstr_start(pos))
    funcs = []
    for p in find_xrefs(elf, v):
        f = elf.func_containing(elf.off2vaddr(p))
        if f and f not in funcs:
            funcs.append(f)
    if not funcs:
        return None, 0, "无交叉引用"
    if unique and len(funcs) > 1:
        return None, len(funcs), f"引用不唯一（{len(funcs)} 个函数）"
    return funcs[0], len(funcs), f"{len(funcs)} 个函数引用"


# ────────────────────────── 反汇编 ──────────────────────────
class Dis:
    def __init__(self, elf):
        self.elf = elf
        self.md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
        self.md.detail = True
        self.cache = {}

    def func(self, v):
        if v in self.cache:
            return self.cache[v]
        end = self.elf.func_end(v)
        off = self.elf.vaddr2off(v)
        code = self.elf.buf[off:off + (end - v)]
        insns = list(self.md.disasm(code, v))
        self.cache[v] = insns
        return insns

    @staticmethod
    def mem_write(ins, reg):
        if len(ins.operands) < 2:
            return None
        d, s = ins.operands[0], ins.operands[1]
        if d.type != capstone.x86.X86_OP_REG or ins.reg_name(d.reg) != reg:
            return None
        if s.type != capstone.x86.X86_OP_MEM:
            return None
        return (ins.reg_name(s.mem.base) if s.mem.base else None, s.mem.disp)

    @staticmethod
    def reg_write(ins, reg):
        if len(ins.operands) < 2:
            return None
        d, s = ins.operands[0], ins.operands[1]
        if d.type != capstone.x86.X86_OP_REG or ins.reg_name(d.reg) != reg:
            return None
        return ins.reg_name(s.reg) if s.type == capstone.x86.X86_OP_REG else None

    def call_targets(self, v):
        out = []
        for ins in self.func(v):
            if ins.mnemonic == "call" and ins.operands:
                op = ins.operands[0]
                if op.type == capstone.x86.X86_OP_IMM:
                    out.append((ins, op.imm))
        return out

    def contains_call(self, v, target):
        return any(t == target for _, t in self.call_targets(v))

    def find_cmp_magic(self, v):
        for ins in self.func(v):
            if ins.mnemonic != "cmp" or "0x44d" not in ins.op_str:
                continue
            for op in ins.operands:
                if op.type == capstone.x86.X86_OP_MEM:
                    return ins, ins.reg_name(op.mem.base), op.mem.disp
        return None

    def slice_back(self, fstart, at_addr, reg, max_steps=12):
        """反向切片：从 at_addr 处沿 reg 的定值链收集内存位移"""
        insns = self.func(fstart)
        idx = {ins.address: i for i, ins in enumerate(insns)}
        i = idx.get(at_addr, 0) - 1
        disps, cur = [], reg
        for _ in range(max_steps):
            found = False
            while i >= 0:
                ins = insns[i]
                m = self.mem_write(ins, cur)
                if m:
                    base, disp = m
                    disps.append(disp & 0xFFFFFFFFFFFFFFFF)
                    if base in ARG_REGS and not any(self.reg_write(insns[j], base) or
                                                    self.mem_write(insns[j], base)
                                                    for j in range(i)):
                        return disps, True
                    cur, i, found = base, i - 1, True
                    break
                r = self.reg_write(ins, cur)
                if r:
                    cur, i, found = r, i - 1, True
                    break
                i -= 1
            if not found:
                return disps, False
        return disps, False


# ────────────────────────── 恢复 ──────────────────────────
def find_version(elf):
    """版本号两条路：新版走 release 串，旧版走 `,x.y.z.<build>` 字面量。"""
    rel = None
    rp = elf.buf.find(A_RELEASE)
    if rp >= 0:
        end = elf.buf.index(b"\x00", rp)
        rel = elf.buf[rp:end].decode("ascii", "replace")
        xyz = rel.rsplit("_", 1)[-1]
        if re.fullmatch(r"\d+\.\d+\.\d+", xyz):
            m = re.search(xyz.replace(".", r"\.").encode() + rb"\.\d{3,6}", elf.buf)
            if m:
                full = m.group(0).decode()
                return int(full.split(".")[-1]), rel, full
    # 旧版（4.0.x / 4.1.0.x）没有 wmpf_release/，只有逗号开头的字面量：\x00,x.y.z.build\x00
    m = re.search(rb"\x00,(\d+\.\d+\.\d+\.\d{3,6})\x00", elf.buf)
    if m:
        full = m.group(1).decode()
        return int(full.split(".")[-1]), rel, full + "（逗号字面量）"
    return None, rel, None


def find_scene_guard(elf, dis, xr, load_start):
    """守卫函数：优先用日志串锚点，失败再用「1101 语义锚点」兜底。"""
    guard_by_str = None
    fs = xr.get(A_GUARD, [])
    if len(fs) == 1:
        guard_by_str = fs[0]

    # 兜底：遍历引用 .cc 文件名的候选函数，找调用了「含 1101 比较的函数」的那个
    guard_by_magic = ls_by_magic = None
    for f in xr.get(A_LOAD_FILE, []):
        for _, tgt in dis.call_targets(f):
            if tgt == f:
                continue
            r = dis.find_cmp_magic(tgt)
            if r:
                guard_by_magic, ls_by_magic = tgt, f
                break
        if guard_by_magic:
            break
    return guard_by_str, guard_by_magic, ls_by_magic


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("binary")
    ap.add_argument("--out", default=None)
    ap.add_argument("--version", type=int, default=None,
                    help="手动指定 WMPF build 号（自动读不到时用，例如旧版 4.0.x 的 11459）")
    ap.add_argument("--cdp-rule", default="auto", choices=["auto", "self", "callee"],
                    help="新旧两版 CDPFilter 取值规则不同：auto 按版本判定，"
                         "self=取字符串 owner 本身（新版），callee=取 owner 的第一个调用（旧版）")
    a = ap.parse_args()

    elf = Elf(a.binary)
    dis = Dis(elf)
    print(f"ELF64 x86-64  {len(elf.buf):,} 字节")
    print(f".text 文件偏移 {elf.text['offset']:#x}  vaddr {elf.text['addr']:#x}  delta {elf.delta:#x}")
    print(f"函数表 {len(elf.starts):,} 个\n")

    # ── 版本 ──
    ver, rel, full = find_version(elf)
    print(f"release 串: {rel or '（无）'}")
    print(f"WMPF 版本 : {full or '读不到'}")
    if a.version is not None:
        ver = a.version
        print(f"  （按 --version 覆盖为 {ver}）")
    elif ver is None:
        print("  ⚠️ 自动读不到版本号。偏移照样算得出来，但产出的 JSON 该叫什么名字得你自己定："
              "先看微信版本（`docker exec $WX cat /config/wechat/.woc-version`），"
              "确认 WMPF 后加 --version <build> 重跑。")
    print(f"  Version={ver}")

    # ── 锚点巡场 ──
    pats = [A_LOAD_FILE, A_LOAD_NAME, A_LOAD_PERF, A_FILTER_NAME,
            A_FILTER_FILE, A_CAST_NAME, A_CAST_FILE, A_GUARD]
    xr = xrefs_many(elf, pats)
    print("\n锚点引用情况：")
    for p in pats:
        fs = xr.get(p, [])
        print(f"  {len(fs)} 个函数引用  {p.decode('ascii', 'replace')}"
              + (f"  -> {[hex(x) for x in fs]}" if 0 < len(fs) <= 4 else ""))

    result = {"Version": ver}

    # ── LoadStart ──
    print("\n== LoadStartHookOffset ==")
    load_start = load_name_fs = None
    if xr.get(A_LOAD_NAME):
        inter = [f for f in xr[A_LOAD_FILE] if f in set(xr[A_LOAD_NAME])]
        print(f"  新版规则（.cc ∩ RTTI 签名串）= {[hex(x) for x in inter]}")
        if len(inter) == 1:
            load_start = load_name_fs = inter[0]
        else:
            print(f"  ⚠️ 交集不唯一，改用旧版锚点")
    if load_start is None:
        fs = xr.get(A_LOAD_PERF, [])
        print(f"  旧版规则（引用 `[Perf] AppletIndexContainer::OnLoadStart`）= "
              f"{[hex(x) for x in fs]}")
        if len(fs) == 1:
            load_start = fs[0]
    if load_start is None:
        print("  ❌ 两条规则都没定位到");  return 1
    print(f"  LoadStartHookOffset = {load_start:#x}")
    result["LoadStartHookOffset"] = hex(load_start)

    # ── 守卫函数 & SceneOffsets ──
    print("\n== SceneOffsets ==")
    guard_s, guard_m, ls_m = find_scene_guard(elf, dis, xr, load_start)
    print(f"  日志串定位的守卫函数  = {guard_s and hex(guard_s)}")
    print(f"  1101 兜底定位的守卫函数 = {guard_m and hex(guard_m)}"
          f"（其调用方 {ls_m and hex(ls_m)}）")
    guard = guard_s if guard_s is not None else guard_m
    if guard is None:
        print("  ❌ 定位不到守卫函数");  return 1
    if guard_s is not None and guard_m is not None:
        print(f"  两法{'一致 ✅' if guard_s == guard_m else '不一致 ⚠️，以日志串锚点为准'}")
    if ls_m is not None and ls_m != load_start:
        print(f"  ⚠️ 1101 兜底给出的 LoadStart 是 {ls_m:#x}，与上面的 {load_start:#x} 不同")

    r = dis.find_cmp_magic(guard)
    if not r:
        print("  ❌ 守卫函数里没有 1101 比较");  return 1
    cmp_ins, cmp_base, cmp_disp = r
    print(f"  魔数比较: {cmp_ins.mnemonic} {cmp_ins.op_str}  @{cmp_ins.address:#x}")
    chain, ok = dis.slice_back(guard, cmp_ins.address, cmp_base)
    callee_part = list(reversed([cmp_disp] + chain))
    print(f"  守卫函数内（off2..off5）= {callee_part}   到达函数参数: {ok}")

    # 找调用方：LoadStart 里调用守卫函数的那一处
    caller_part, ok2 = None, False
    for ins, tgt in dis.call_targets(load_start):
        if tgt == guard:
            ch, ok2 = dis.slice_back(load_start, ins.address, "rdi")
            caller_part = list(reversed(ch))
            break
    print(f"  调用方内（off0..off1）  = {caller_part}   到达函数参数: {ok2}")
    scene = (caller_part or []) + callee_part
    print(f"  SceneOffsets = {scene}")
    result["SceneOffsets"] = scene

    # ── CDPFilter：新旧两条规则 ──
    print("\n== CDPFilterHookOffset ==")
    owner_s = xr.get(A_FILTER_NAME, [])
    if len(owner_s) != 1:
        print(f"  ❌ `SendToClientFilter` 引用函数不唯一（{len(owner_s)} 个）");  return 1
    owner = owner_s[0]
    inter = [f for f in owner_s if f in set(xr.get(A_FILTER_FILE, []))]
    cand_self = inter[0] if len(inter) == 1 else None
    calls = dis.call_targets(owner)
    cand_callee = calls[0][1] if calls else None
    print(f"  字符串 owner = {owner:#x}")
    print(f"  规则 self  （取 owner 本身，且 owner 同时引用 .cc 名） = "
          f"{cand_self and hex(cand_self)}")
    print(f"  规则 callee（取 owner 的第一个调用）            = "
          f"{cand_callee and hex(cand_callee)}")

    if a.cdp_rule == "self":
        rule = "self"
    elif a.cdp_rule == "callee":
        rule = "callee"
    else:
        # 上游的划分：新版布局里 OnLoadStart 的 RTTI 签名串在场；旧版不在场。
        rule = "self" if xr.get(A_LOAD_NAME) else "callee"
        print(f"  auto 判定：`AppletIndexContainer::OnLoadStart(bool` "
              f"{'在场 → 新版' if xr.get(A_LOAD_NAME) else '不在场 → 旧版'}，取规则 {rule}")
    cdp = cand_self if rule == "self" else cand_callee
    if cdp is None:
        print(f"  ❌ 规则 {rule} 取不到值");  return 1
    print(f"  CDPFilterHookOffset = {cdp:#x}")
    result["CDPFilterHookOffset"] = hex(cdp)

    # 语义交叉校验（legacy 专用）：legacy 补丁在 onLeave 里读 retval+8 判断是不是 6，
    # 所以它真正要补的是「返回那个 +8 == 6 结构」的函数。在 owner 里找
    # `call X` 紧跟 `cmp dword ptr [rax + 8], 6` 的形态，X 就是它。
    sem = None
    ins_list = dis.func(owner)
    for i, x in enumerate(ins_list):
        if x.mnemonic != "call" or not x.operands or x.operands[0].type != 2:
            continue
        for y in ins_list[i + 1:i + 8]:
            if y.mnemonic == "call":
                break
            s = y.op_str.replace(" ", "")
            if y.mnemonic == "cmp" and s.endswith(",6") and "+8]" in s:
                sem = x.operands[0].imm
                break
        if sem:
            break
    if sem is not None:
        mark = "与上面取值一致 ✅" if sem == cdp else "与上面取值不同 ⚠️"
        print(f"  语义校验：owner 里 `call X; cmp [rax+8], 6` 的 X = {sem:#x}  {mark}"
              f"（legacy 补丁真正要补的就是它）")
    else:
        print("  语义校验：owner 里没有 `call X; cmp [rax+8], 6` 形态"
              "（新版布局用四参数 replace，本就不需要它）")

    # ── CastToJson ──
    # hook.js 只靠「配置里有没有 CastToJsonHookOffset」来选挂钩方式：
    #   有 → 用四参数回调整段 replace（目标该是 owner 本身）
    #   没有 → 退回 legacy 的 Interceptor.attach + onLeave(retval+8 == 6)（目标该是 owner 的第一个调用）
    # 所以这一项必须和上面取的 CDPFilter 配套输出，否则会把 hook.js 带到另一条路上。
    print("\n== CastToJsonHookOffset ==")
    cast = [f for f in xr.get(A_CAST_NAME, []) if f in set(xr.get(A_CAST_FILE, []))]
    if len(cast) != 1:
        print(f"  ❌ `CastToJson` ∩ `devtools_message_filter.cc` 不唯一："
              f"{[hex(x) for x in cast]}");  return 1
    print(f"  找到 CastToJson 函数 = {cast[0]:#x}")
    if rule == "self":
        result["CastToJsonHookOffset"] = hex(cast[0])
        print("  新版布局 → 输出。hook.js 见到它就走「四参数整段替换」，与上面取的 owner 配套")
    else:
        print("  旧版布局 → **故意不输出**。上面按 legacy 取了「第一个调用」，"
              "若再把这一项写进配置，\n"
              "     hook.js 会改走四参数整段替换（它的四参数签名与那个两参 worker 对不上）。")

    # ── 自检 ──
    print("\n== 自检 ==")
    bad = [k for k, v in result.items()
           if isinstance(v, str) and not elf.is_func_start(int(v, 16))]
    if bad:
        print(f"  ❌ 这些不是函数起始，结果可疑: {bad}");  return 1
    print("  三个偏移均为合法函数起始 ✅")
    if len(result["SceneOffsets"]) != 6:
        print(f"  ❌ SceneOffsets 不是 6 个值：{result['SceneOffsets']}");  return 1
    print("  SceneOffsets 为 6 个值 ✅")
    if dis.find_cmp_magic(guard) is None:
        print("  ❌ 守卫函数里找不到 1101 比较");  return 1
    print("  守卫函数含 1101 魔数比较 ✅")

    out = json.dumps(result, indent=4)
    print("\n== 产出 ==")
    print(out)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(out + "\n")
        print(f"\n已写入 {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
