"""锚点巡检：报告 WMPF 运行时二进制里各锚点串是否在场、出现几次。

用途：换版本时先跑一遍，就知道 recover_offsets.py 的哪条路径能用、哪条要换锚点。
不要求 ELF —— 也能读 Windows 的 flue.dll，用来单独验证「锚点还在不在」。

用法: python survey.py <WeChatAppEx | flue.dll 路径>
"""
import re
import struct
import sys

ANCHORS = [
    b"wmpf_release/",
    b"applet_index_container.cc",
    b"AppletIndexContainer::OnLoadStart(bool",
    b"[Perf] AppletIndexContainer::OnLoadStart",
    b"SendToClientFilter",
    b"devtools_message_filter_applet_webview.cc",
    b"CastToJson",
    b"devtools_message_filter.cc",
    b"create webview devtools failed.",
]


def elf_sections(buf):
    e_shoff, = struct.unpack_from("<Q", buf, 40)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", buf, 58)
    secs = []
    for i in range(e_shnum):
        sh = struct.unpack_from("<IIQQQQIIQQ", buf, e_shoff + i * e_shentsize)
        secs.append({"name_off": sh[0], "addr": sh[3], "offset": sh[4], "size": sh[5]})
    st = secs[e_shstrndx]
    strtab = buf[st["offset"]:st["offset"] + st["size"]]
    for s in secs:
        s["name"] = strtab[s["name_off"]:strtab.index(b"\x00", s["name_off"])].decode("ascii", "replace")
    return secs


def main():
    path = sys.argv[1]
    buf = open(path, "rb").read()
    print(f"{path}: {len(buf):,} 字节")

    is_elf = buf[:4] == b"\x7fELF"
    if is_elf:
        print(f"ELF class={'ELF64' if buf[4] == 2 else 'ELF32'}  "
              f"machine={struct.unpack_from('<H', buf, 18)[0]:#x}")
        secs = elf_sections(buf)
        for n in (".text", ".rodata", ".eh_frame_hdr"):
            for s in secs:
                if s["name"] == n:
                    print(f"  {n:16} off={s['offset']:#x} vaddr={s['addr']:#x} size={s['size']:#x}")
        for s in secs:
            if s["name"] == ".text":
                print(f"  delta(.text vaddr-fileoff) = {s['addr'] - s['offset']:#x}")
            if s["name"] == ".eh_frame_hdr":
                b = buf[s["offset"]:s["offset"] + s["size"]]
                print(f"  .eh_frame_hdr 编码 {b[1]:#x}/{b[2]:#x}/{b[3]:#x}  "
                      f"fde_count={struct.unpack_from('<I', b, 8)[0]:,}")
                if not (b[1] == 0x1B and b[2] == 0x03 and b[3] == 0x3B):
                    print("  ⚠️ 编码不常见，recover_offsets.py 会直接报错")
    elif buf[:2] == b"MZ":
        print("PE（Windows）—— 只能查锚点在场情况，不算偏移")
    else:
        print("既不是 ELF 也不是 PE")

    print("\n锚点：")
    for a in ANCHORS:
        n = buf.count(a)
        print(f"  {n:4d} ×  {a.decode('ascii', 'replace')}")

    print("\n版本串：")
    rp = buf.find(b"wmpf_release/")
    if rp >= 0:
        end = buf.index(b"\x00", rp)
        rel = buf[rp:end].decode("ascii", "replace")
        print(f"  release: {rel}")
        xyz = rel.rsplit("_", 1)[-1]
        if re.fullmatch(r"\d+\.\d+\.\d+", xyz):
            for m in re.finditer(xyz.replace(".", r"\.").encode() + rb"\.\d{3,10}", buf):
                print(f"  完整版本: {m.group(0).decode()}")
    else:
        print("  没有 wmpf_release/ 串")
    return 0


if __name__ == "__main__":
    sys.exit(main())
