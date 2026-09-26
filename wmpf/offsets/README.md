# 自己算 WMPF hook 偏移

微信一升级，WMPF 运行时换个版本，上游 WMPFDebugger 里没有对应偏移配置就挂不上 hook。
这个目录里的工具直接从 `WeChatAppEx` 二进制里把偏移算出来，不用等别人发 PR。

```bash
pip install capstone numpy
python recover_offsets.py <WeChatAppEx 路径> --out addresses.<版本>.json
```

产出的 JSON 就是 hook 容器里
`$WMPF_DIR/frida/config/linux/addresses.<版本>.json`（默认 `WMPF_DIR=/opt/wmpf`）的内容，
放进去即可用（`<版本>` 取 `Version` 字段，例如 25665）。

> 通常不用手动搬 —— `bash wmpf/auto_offsets.sh` 会把「抠二进制 → 算 → 判卷 → 装进去」
> 一条命令走完，并在最后提示你重启 hook。

## 判卷：5 个真机二进制上跑过

不是「看着像对」，是逐字段与上游**人工适配**的配置比对。样本跨了三代
（WMPF 1.1→2.5，微信 4.0.0.30 → 4.1.13.23）：

| 二进制 | WMPF | 判卷结果 |
|---|---|---|
| 微信 4.1.13.23 / 4.1.13.9 | 2.5.6.**25665** | **5/5 全一致** ✅ |
| 微信 4.1.1.8 | 2.2.4.**14978** | **4/4 全一致** ✅ |
| 微信 4.1.1.4 | 2.2.4.**14910** | 4 项里 3 项一致，`CDPFilterHookOffset` 不同 ⚠️ |
| 微信 4.1.0.13 | 2.2.4.**14664** | 上游没有配置，自检全过 |
| 微信 4.0.0.30 | 2.1.4.**11459** | 上游没有配置，自检全过 |

「自检全过」= 三个偏移都是合法函数起始、`SceneOffsets` 是 6 个值、守卫函数里确有 `1101` 比较。

> **真机验证（2026-09-24，逐个版本装进容器实跑）**：静态判卷只说明「算得对」，不等于「能用」。
> 判据是端到端：打开小程序 → hook 接管 → CDP 刷出 token → 签到接口返回 200/415。
>
> | 微信版本 | WMPF | 真机结果 |
> |---|---|---|
> | 4.1.13.23 | 25665 | ✅ 当天签到成功（200，复跑 415） |
> | 4.1.1.8 | 14978 | ✅ 刷出 token，签到接口 415 |
> | 4.1.1.4 | 14910 | ✅ 同上（两种 `CDPFilterHookOffset` 都可用，见下） |
> | 4.0.0.30 | 11459 | ✅ 同上，但需给上游补版本探测回退（见 `hook_patch.sh`） |
> | 4.1.0.13 | 14664 | ❌ 微信服务端拒绝登录（提示「版本过低」），偏移算得出也登不进去 |
>
> **14910 那项差异的结论要收窄**：两份配置在真机上都跑通了——都能刷出 token、签到接口都回 415，
> 所以「上游那份装上去会崩」的推断**不成立**。差异只体现在 hook 的 legacy 分支日志上：
> 我们的地址在几次会话里没被调用过，上游的 owner 被调用了 1 次但读到 `retval+8 = 0`，
> 两者都没走到 `CDP filter patched`。也就是说这个字段在当前 CDP 用法下**不影响可用性**。

> **14910 那份为什么差一项**：同一套代码布局下，上游两份旧配置口径不一致——
> `addresses.14978.json` 取「owner 的第一个调用」，`addresses.14910.json` 取「owner 本身」。
> 按上游 `ADAPTATION.md` 写的规则、以及 legacy 补丁的实际语义，都指向「第一个调用」：
> 补丁要在 `onLeave` 里读 `retval + 8` 判断是不是 6，而**返回那个 `+8 == 6` 结构的正是第一个调用**；
> owner 自己也只是把这个返回值拿去 `cmp` 分支。本工具跟的是「第一个调用」。
> （真机上两种取值都能跑通——所以这是**规则口径**的差异，不影响可用性，见上面的实测。）

```bash
# 与上游那份人工适配的配置比一下（上游有的话）
python judge.py addresses.25665.recovered.json upstream.25665.json
python verify_batch.py <放二进制的目录>    # 批量跑 + 自动判卷
```

> `judge.py` 的第二个参数是上游配置，从 hook 容器里取：
> `docker cp woc-hook:/opt/wmpf/frida/config/linux/addresses.<版本>.json upstream.<版本>.json`

本目录里**只有我们自己算出来的那两份**（`addresses.11459` / `addresses.14664`.recovered.json），可以直接对照；
上游自带的三版（14910 / 14978 / 25665）**不放进来** —— 那是上游 WMPFDebugger 的文件，版权不归本项目。

## 五条恢复规则

规则与上游 `frida/autodetect/win32.js` 采用的判据一致（找字符串锚点 + 反查引用），
实现路径不同：这里把 frida 换成离线 ELF 解析，并补上了 Linux 两份二进制实测出来的差异。

| 字段 | 怎么找 |
|---|---|
| `Version` | 新版：`wmpf_release/<tag>_<x.y.z>` 取出 `x.y.z`，再找 `x.y.z.<build>`（tag 如 `xwechat_2026T6`、`linux_2025T4`）<br>旧版（4.0.x 没有 release 串）：找 `,x.y.z.<build>` 逗号字面量<br>两条都读不到时用 `--version` 手动指定（偏移照样算得出来） |
| `LoadStartHookOffset` | 新版：同时引用 `applet_index_container.cc` 与 `AppletIndexContainer::OnLoadStart(bool` 的**唯一**函数<br>旧版：引用 `[Perf] AppletIndexContainer::OnLoadStart` 的函数 |
| `SceneOffsets` | 守卫函数 = 引用 `create webview devtools failed.` 的函数；从它内部的 `cmp [reg+X], 1101` 反向切片取 6 层字段偏移 |
| `CDPFilterHookOffset` | 新版：同时引用 `SendToClientFilter` 与 `devtools_message_filter_applet_webview.cc` 的唯一函数<br>旧版：该函数调用的**第一个**函数<br>两种情况下工具都会另做一遍**语义交叉校验**（见下），两个候选也会都打印，`--cdp-rule` 可强制指定 |
| `CastToJsonHookOffset` | 同时引用 `CastToJson` 与 `devtools_message_filter.cc` 的唯一函数 |

`1101`（`0x44d`）是官方文档点名的场景判定魔数，也是整条指针链的语义锚点，所以能靠它反向切片。

> 新版 `CDPFilterHookOffset` 默认取 owner 本身是有依据的，不是二选一随便挑：
> hook 那边是用 `Interceptor.replace` 拿一个**四参数**回调（`jsonOut, thiz, cborInput, cborLen`）
> 把目标函数整段替换掉。owner 开头就把 `rdi/rsi/rdx/rcx` 四个参数都存了下来（`rdi` 存进 r13
> 后面还要用），内部再转调一个两参 worker；而 worker 开局十几条指令里**根本没碰 `rdi`**，
> 没把第一个参数当可写的 json 出参。回调签名对得上 owner，对不上 worker。
> 旧的 14978 布局才按「第一个调用」取。
>
> 还有两条配套规则，都是让产出的配置跟 hook.js 的判定**配套**：
>
> ① **旧版布局不输出 `CastToJsonHookOffset`**。hook.js 只认这个字段在不在来选挂钩方式：
> 有它 → 四参数整段替换（目标该是 owner）；没有 → legacy 的 `onLeave(retval + 8 == 6)`（目标该是
> 第一个调用）。旧版既然按 legacy 取了第一个调用，就不能把这个字段写进去，否则 hook.js 会被
> 带到另一条路上。所以产出的字段集合也对齐年代：**新版 5 项、旧版 4 项**，与上游同年代的配置同形。
>
> ② **旧版还会做一遍语义校验**：在 owner 里找 `call X` 紧跟 `cmp dword ptr [rax + 8], 6` 的形态
> —— legacy 补丁在 `onLeave` 里读的就是 `retval + 8` —— 那个 X 就是真正要补的函数。
> 实测四个旧版样本（11459 / 14664 / 14910 / 14978）上，它都与「第一个调用」的取值一致；
> 新版样本 25665 上两者不同，工具会说明「新版走四参数替换，legacy 形态本就不适用」。

## 三个坑

1. **坐标是 vaddr，不是文件偏移**。`.text` 的 `vaddr − 文件偏移 = 0x1000`，差这一点全盘皆错。
2. **锚点必须回溯到 C 字符串起点**。搜 `applet_index_container.cc` 命中的可能是字符串中间某处，
   而代码引用的是开头（编译器字符串池会把路径做成更长串的后缀）。
3. **源文件名字符串不唯一**。同一个 `.cc` 会被几十个函数引用，靠它定位会撞一堆干扰；
   要用「**名字串 ∩ 文件名串**求交集」来收敛到唯一函数——这也是上游的做法。

## 二进制大改，锚点几乎不动

14978 → 25665 是跨了一代（WMPF 2.2.4 → 2.5.6），二进制改动很大：

| | 2.2.4.14978（4.1.1.8） | 2.5.6.25665（4.1.13.23） | 变化 |
|---|---|---|---|
| 文件大小 | 211,307,216 | 243,670,136 | **+15.3%** |
| `.text` 大小 | 153.9 MB | 173.4 MB | **+12.7%** |
| 函数个数 | 420,525 | 483,329 | **+14.9%** |
| 5 个偏移**值** | — | — | **全都不一样** |

但用来定位的锚点基本没动。**8 条锚点在 5 个 Linux 二进制（跨 WMPF 11459 → 25665）
外加一个 Windows 的 25715 上都在场，而且每条只出现 1 次**（除新旧互斥的那两条）：

| 锚点 | 旧版线<br>11459 / 14664 / 14910 / 14978 | 新版线<br>25665 / 25715 |
|---|---|---|
| `SendToClientFilter` | 有 | 有 |
| `devtools_message_filter_applet_webview.cc` | 有 | 有 |
| `CastToJson` / `devtools_message_filter.cc` | 有 | 有 |
| `applet_index_container.cc` | 有 | 有 |
| `create webview devtools failed.` | 有 | 有 |
| 魔数 `1101` | 有 | 有 |
| `AppletIndexContainer::OnLoadStart(bool` | **无** | 有 |
| `[Perf] AppletIndexContainer::OnLoadStart` | **有** | **无** |
| 版本串形态 | `\x00,<x.y.z>.<build>\x00`（如 `,2.1.4.11459`） | `wmpf_release/<tag>_<x.y.z>` 再找 `x.y.z.<build>` |

（25715 那份是 Windows 的 `flue.dll`，只用来看锚点在不在场；本工具算偏移只做 ELF。
另外 4.0.x 那代没有 `wmpf_release/` 串，版本号只能走逗号字面量；工具两条路都实现了。）

一共只变了三件事，而且都是**可判定**的，不用人肉眼比：

1. release 串前缀：`linux_2025T4_` → `xwechat_2026T6_` → `xwechat_2026T7_`（版本号照样读得出来）
2. LoadStart 的锚点串换了个名字（两串互斥，谁在场用谁）
3. CDPFilter 的取值规则变了（新版取字符串 owner 本身，旧版取 owner 的第一个调用）

所以「偏移每版都变」和「锚点能不能自动找」是两件事：**值必须重算，锚点不用重找**。
人工适配累的地方在于一版一版重新对地址，而这一步正是可以自动化的。

## 什么情况下这条路会走不通

先说结论：**会先坏的不是锚点，是配置的形态**。

值每版都变 → 能自动重算。锚点被大重构抹掉（日志串改名或删除、`1101` 魔数变更、
筛选函数被整体内联）→ 工具**报错退出**，需要人工重新锚定；它不会静默给错值
（自检项：三个偏移必须是函数起始、`SceneOffsets` 必须是 6 个值、守卫函数里必须有 `1101` 比较）。

**配置形态换代是另一种坏法，而且已经发生过一次**：从 WMPF 25715 起，配置不再用
`SceneOffsets` 六元组，改成结构化的 `MiniAppConfigStructOffsets`，多出
`WebSocketURLStringOffset`、`RemoteDebugModeOffset` 两个字段。本工具目前只产出前者，
真遇到那种版本，**光重算值不够，得扩展工具**。
（Linux 已知的运行时都还是 `SceneOffsets` 形态，暂时不受影响——但这条要记着。）

其余边界：

- 只支持 **x86-64 / SysV ABI**，只处理 `.eh_frame_hdr` 的常见编码组合（`0x1b/0x03/0x3b`），
  遇到别的编码会明确报错。
- 拿不准就先跑 `survey.py` 看锚点在不在场，或者**不加 `--out` 跑一遍**看每步判定，再落盘。

真要兜底，还有两条不用逆向的路：把微信钉回已知可用的旧版（`fetch_wechat_deb.sh`），
或换 Windows 方案（上游 win32 有 53 份现成配置）。

## 依赖

`capstone` + `numpy`，只在分析阶段需要；产出是纯 JSON，跑 hook 时不需要。

## 致谢

规则来自 [WMPFDebugger](https://github.com/evi0s/WMPFDebugger)（GPLv2）的 `ADAPTATION.md` 与
`frida/autodetect/win32.js`——上游是运行期（frida）实现且只支持 Windows，这里是同一个思路的离线版。
