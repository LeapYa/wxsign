# 部署：微信容器 + hook + 青龙

本合集跑起来要三样东西：**一个跑着微信的 Linux 容器**（签到脚本从它里面取登录态）、
**一个旁挂的 hook 容器**（读小程序的 JS 层，用来拿 jsCode 和 appId）、
**青龙面板**（定时调度）。

**硬性前提：三者必须在同一台宿主机上。** token 靠微信客户端当场产生，服务端没法自举。

资源参考：微信实例 1.2 GiB（跑起小程序约 1.8 GiB）+ hook 0.47 GiB + 面板 0.12 GiB + 青龙 0.2 GiB，
合计约 **2.5 GiB 内存、6 GB 磁盘**；建议 **2 核 4 GiB** 起。

> **本文的占位符约定**
>
> - `$WX` = 微信实例容器名。云微起的实例都叫 `woc-wx-` 开头，用这条自动取：
>   ```bash
>   WX=$(docker ps --format '{{.Names}}' | grep '^woc-wx-' | head -1); echo $WX
>   ```
> - `<...>` = 需要你替换的值（如面板密码），出现处都会说明它从哪来。

---

## 1. 装 Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER && newgrp docker
```

---

## 2. 起微信容器（云微 WechatOnCloud）

云微是目前唯一内置完整设备伪装的方案（唯一且持久的 machine-id、真实 hostname、
移除 `/.dockerenv`、真实 OUI 的 MAC、可开关的 OS 伪装、面板一键「重置设备 ID」）。
不做伪装的话，容器里的微信很容易被判定为设备农场，表现为**登录后立刻被踢下线、反复循环**。

### 2.1 起面板与实例

```bash
mkdir -p ~/woc && cd ~/woc
curl -fsSLO https://raw.githubusercontent.com/Gloridust/WechatOnCloud/main/docker-compose.yml
```

编辑 `docker-compose.yml`：**删掉 `- /dev:/host-dev:ro`**（无摄像头时不需要，部分环境挂载会失败）。

生成面板密码并记下来：

```bash
PW="woc-$(head -c 12 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 10)"
echo "面板密码: $PW"
```

建 `.env`：

```dotenv
WOC_PASSWORD=粘贴上一步 echo 出来的那串
WOC_HTTP_PORT=36080
WOC_SPOOF_OS=1
# 默认软阈值 1500MiB 太低（跑起小程序实测到 1.8G），看门狗会「柔和重启」实例，
# 而重启 = 微信掉登录（要手机确认）+ hook 容器被连坐带走
WOC_INSTANCE_MEM_SOFT_MB=2800
WOC_INSTANCE_MEM_HARD_MB=4000
```

```bash
docker compose up -d
```

> **坑**：`docker compose` 报 WSL 相关错误（Windows 上常见）时改用等价的 `docker run`，
> 但**面板与实例必须同一个自定义网络** —— 默认 bridge 没有容器名 DNS，
> 面板按容器名反代实例会一直卡在「桌面长时间未就绪」：
> ```bash
> docker network create woc-net
> docker run -d --name woc-panel --network woc-net -p 36080:8080 \
>   -v ~/woc/data-panel:/data -v /var/run/docker.sock:/var/run/docker.sock \
>   -e PORT=8080 -e WOC_DOCKER_NETWORK=woc-net \
>   -e WOC_WECHAT_IMAGE=docker.io/gloridust/wechat-on-cloud:1.4.9 \
>   -e PANEL_ADMIN_USER=admin -e PANEL_ADMIN_PASSWORD=同上那串密码 \
>   -e WOC_SPOOF_OS=1 -e WOC_INSTANCE_MEM_SOFT_MB=2800 -e WOC_INSTANCE_MEM_HARD_MB=4000 \
>   -e TZ=Asia/Shanghai --restart unless-stopped gloridust/woc-panel:latest
> ```

### 2.2 建实例并登录

浏览器打开 `http://<机器IP>:36080`（用户名 `admin`）→ 新建「微信实例」→ 等镜像拉完、
微信自动装好 → 进实例 → **手机扫码登录**（建议用闲置小号）。

> 容器画面在面板里看（KasmVNC 网页），扫码和后面万一要人工确认都在这上面做。

> 首次必然是**扫码**（这时还没有登录态）。之后只要实例不重启就一直有效 ——
> 掉登录只发生在实例重启后，而重启后点「登录」**还得手机确认**（Linux 版没有免确认选项）。
> 详见 [第 6 节](#6-微信侧常见故障这部分是运维的大头) 开头的「登录这件事」。

**忘了面板密码**：

| 情况 | 做法 |
|---|---|
| 正常 | `grep WOC_PASSWORD ~/woc/.env` |
| `.env` 没了 | `docker inspect woc-panel --format '{{range .Config.Env}}{{println .}}{{end}}' \| grep PANEL_ADMIN` |
| 没设过 | 官方 compose 默认 `WOC_PASSWORD=wechat`。**别用默认值** —— 这面板能操作宿主 Docker |
| 想换 | 改 `~/woc/.env` 后重建面板；微信实例在数据卷里，不受影响 |

> 注意本方案有**两个 `.env`**，别搞混：`~/woc/.env` 是**云微面板**的；
> `brands/<slug>.env` 是**签到脚本**的。青龙环境变量页里配的是后者那类。

### 2.3 装微信时就把版本钉住（建议先做）

云微面板默认装**最新版**微信，而 hook 需要对应的 WMPF 偏移配置 —— 最新版未必有
（下一节要验的就是这个）。所以最好**建实例时就指定版本**，别等挂不上再回退：

```bash
bash wmpf/fetch_wechat_deb.sh 4.1.13.23 ./wechat-cdn    # 按版本下载 + 校验 sha256
```

把 `./wechat-cdn` 挂到任意能 HTTP 访问的地方（nginx / 对象存储 / 临时用
`python3 -m http.server 8000` 都行），建实例时加一个环境变量（⚠️ 指的是**目录**，不带文件名）：

```
WECHAT_CDN=https://<你的域名>/<指向该目录的路径>
```

> ⚠️ **不要点面板里的「更新微信」** —— 它下的是官网那条**不带版本号**的直链，永远拿到最新版。
> 哪些版本已知可用、各自对应哪个 WMPF，见下一节表格。

### 2.4 先验 WMPF 版本（不通过就别往下走）

hook 靠 frida 找 WMPF 内部的偏移，**WMPF 版本必须落在 WMPFDebugger 的偏移配置里**，否则挂不上。
⚠️ **要看的是 WMPF 版本，不是微信版本号**（多个微信版本可能共用同一个 `WeChatAppEx`）。

一条命令验完（纯只读：只 `docker exec` 读文件，不重启、不影响登录态）：

```bash
bash wmpf/check_wmpf.sh $WX
```

看到 `[OK] WMPF 25665 有对应偏移配置` 再继续；看到 `[FAIL]` 先别往下走。

#### 目前已知可用的组合

| 微信 Linux | WMPF | 偏移配置来自 | 状态 |
|---|---|---|---|
| **4.1.13.23**（4.1.13.9 的 `WeChatAppEx` 字节完全相同） | 2.5.6.**25665** | 上游自带 | ✅ 真机跑通 —— **本项目的默认版本** |
| 4.1.1.8 | 2.2.4.**14978** | 上游自带 | ✅ 真机跑通 |
| 4.1.1.4 | 2.2.4.**14910** | 上游自带 | ✅ 真机跑通 |
| 4.0.0.30 | 2.1.4.**11459** | 本仓库自算 | ✅ 真机跑通（另需上游那两条补丁，见 [第 4.1 节](#41-必须打的两个补丁)） |
| 4.1.0.13 | 2.2.4.**14664** | 本仓库自算 | ❌ 偏移算得出，但**微信服务端拒绝登录**（提示「版本过低」） |

**「上游自带」三份不用管** —— hook 装好就已经在 `frida/config/linux/` 里了。
**「本仓库自算」两份**的产出留在 [`wmpf/offsets/`](../wmpf/offsets/)
（`addresses.11459.recovered.json` / `addresses.14664.recovered.json`），可直接对照。

> 上游自带的那三份**没有**放进本仓库 —— 它们是上游 WMPFDebugger 的文件副本，
> 版权不归本项目（见 [README 第九节](../README.md#关于授权源码公开但不是开源)）。要用就装上游。

#### 微信升级了、上游还没适配 —— 四条路

**这件事迟早会发生**：微信会一直出新版，而上游 WMPFDebugger 是人工适配的，必然滞后。
按成本从低到高：

**① 先看上游有没有人已经适配了（2 分钟，最省事）**

```bash
bash wmpf/check_upstream.sh 26123        # 把 26123 换成你实际的 WMPF 版本号
```

它列四样：上游现有的 linux 配置清单、**开放中的 PR**（经常有「已经算好了、只是没合并」）、
最近更新的 issue、以及直接搜你那个版本号的命中。
有的话把 PR 里的 JSON 抄下来，放进 hook 容器即可：

```bash
docker cp addresses.<版本>.json woc-hook:/opt/wmpf/frida/config/linux/
bash wmpf/restart_hook.sh
```

**② 自己算（本仓库自带工具，不用等任何人）**

```bash
bash wmpf/auto_offsets.sh $WX    # 抠二进制 → 算偏移 → 判卷 → 装进 hook 容器
bash wmpf/restart_hook.sh        # 重启 hook 生效
```

为什么这条路成立：**偏移的「值」每版都变，但用来定位的「锚点」几乎不动**（跨 WMPF
11459→25665 五代实测，8 条锚点都在场且唯一）。所以「重新对地址」这一步可以自动化 ——
上游累的正是这一步。原理、五条恢复规则、判卷方式见
[`wmpf/offsets/README.md`](../wmpf/offsets/README.md)。

两个已知边界（工具会自己报，不会静默给错值）：

- 锚点真被大改（日志串改名、`1101` 魔数变更、筛选函数被内联）→ 工具**报错退出**，需人工重新锚定；
- **配置形态已经换过一代**：从 WMPF 25715 起不再用 `SceneOffsets` 六元组，改结构化
  `MiniAppConfigStructOffsets`。真遇到那种版本，光重算值不够，得扩展工具。
  （Linux 已知运行时都还是旧形态，暂时不受影响。）

需要宿主机有 `python3` + `capstone` + `numpy`，只在算的时候用，跑 hook 不需要。

**③ 把微信钉回已知可用的旧版**

```bash
bash wmpf/fetch_wechat_deb.sh 4.1.13.23 ./wechat-cdn   # 按版本下载 + 校验 sha256
```

把该目录挂到任意静态服务，启动实例时加 `-e WECHAT_CDN=https://<你的域名>/<目录路径>`。
⚠️ 钉旧版有下限：**微信服务端会拒过旧的客户端**（实测 4.1.0.13 点登录直接提示「版本过低」），
建议钉在 4.1.1.x 或更新。切版本**不丢登录**（登录数据在 `/config/.xwechat`，与程序目录分开）。

**④ 换 Windows 方案** —— 上游 win32 有 53 份现成配置，linux 只有 3 份。
代价是得常开一台 Windows 机器。见 `wmpf/offsets/README.md` 末尾。

---

### 2.5 让小程序以「手机竖版」运行（建议做）

云微镜像的 openbox 有一条**通配**规则，把所有新窗口无条件最大化：

```xml
<!-- /etc/xdg/openbox/rc.xml 最后一行 -->
<application class="*"><maximized>yes</maximized></application>
```

后果：小程序窗口被拉成整个虚拟屏（1280x1024）。页面于是按**桌面宽屏**布局渲染 ——
「立即签到」那种按钮会被拉成 1000+px 宽的巨条，元素位置也和真机差很远。
（这不是微信的问题，也不是「Linux 版就这样」，就是这一条配置。）

改成**只最大化「微信」**（主窗口与小程序面板的标题都叫「微信」，小程序窗口标题是品牌名，
正好能区分）：

```bash
# ① 备份（可回滚）
docker exec $WX sh -c 'cp /etc/xdg/openbox/rc.xml /config/.config/openbox/rc.xml.orig.bak'
# ② 改规则
docker exec $WX sh -c 'sed -i "s|<application class=\"\*\"><maximized>yes</maximized></application>|<application name=\"微信*\"><maximized>yes</maximized></application>|" /etc/xdg/openbox/rc.xml'
# ③ 放到 HOME（= /config，挂载卷）优先位置，容器重建也不丢
docker exec $WX sh -c 'cp /etc/xdg/openbox/rc.xml /config/.config/openbox/rc.xml'
# ④ 重载
docker exec $WX sh -c 'DISPLAY=:1 openbox --reconfigure'
```

实测结果：小程序窗口变成 **410x776 @ (435,124)** 的手机竖版，页面按 410 CSS px 布局；
主窗口与面板仍是 1280x1024（它们的标题命中「微信*」）。

> ⚠️ `--reconfigure` **不会重排已经打开的窗口**。已有的小程序窗口要关掉重开才生效；
> 如果面板窗口是在改规则**之前**创建的，它会保持原样，但下次重开就会按新规则最大化。

**回滚**：

```bash
docker exec $WX sh -c 'cp /config/.config/openbox/rc.xml.orig.bak /etc/xdg/openbox/rc.xml && cp /etc/xdg/openbox/rc.xml /config/.config/openbox/rc.xml && DISPLAY=:1 openbox --reconfigure'
```

#### 这对脚本意味着什么（重要）

小程序窗口不再铺满屏幕、**位置也不在 (0,0)** → 所有点击与截图都必须加**窗口原点**：

```
屏幕坐标 = 窗口原点 + 页面坐标
```

换算这么简单，是因为实测（用 CDP 问页面自己）：

```
window.screenX/screenY = (435,124)   ← 与 xdotool 报的窗口原点完全一致
innerHeight(779) > outerHeight(776)  ← 顶部那条「⌂ 首页 ●●● ─ ⊙」是覆盖层，不占视口
devicePixelRatio = 1
```

所以**不需要**补偿标题栏高度。脚本已按此实现：`wxwin.py` 负责取窗口几何，
`wxreg.py` 的 `click()/grab()/grab_png()` 全部走页面坐标并自动加偏移。

> 不开这一节也能跑（窗口铺满时页面坐标恰好等于屏幕坐标），但那纯属巧合：
> 一旦窗口位置变化就全错。开了之后是**正确**的写法。

#### 两条路取位置，CDP 优先

`wxreg` 拿「页面在哪、多大」有两条路，**先问页面自己**：

1. **CDP**：`window.screenX / screenY / innerWidth / innerHeight` —— 这是向前兼容的关键。
   将来小程序若像 Windows 版那样改成**侧边栏 / 分栏**（嵌在主窗口里、不居中），
   按窗口标题找「独立小程序窗口」会**直接失败**，但页面自己照样报得出自己的位置与尺寸。
2. **回退**：按窗口标题找独立小程序窗口（`wxwin.rect()`）。

实测两者略有差别（CDP 报视口高 779、窗口报 776）—— CDP 更准，因为它不含窗口边框。

标题栏上那个「返回 / 回首页」按钮同理：**探测**标题栏带里**最靠左的紧凑字形块**
（`wxwin.glyphs()`），不写死比例坐标 —— 换图标 / 换字号 / 换主题色都不影响，
探测不到才退回经验比例。实测探测出 (18,43)，与手工点中的 (23,40) 是同一个图标。

## 3. 装青龙

```bash
mkdir -p ~/ql && cd ~/ql
docker run -d --name qinglong \
  -p 5700:5700 \
  -v ~/ql/data:/ql/data \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v $(which docker):/usr/bin/docker \
  --restart unless-stopped \
  whyour/qinglong:latest
```

> **两处挂载不能省**：脚本靠 `docker exec` / `docker cp` 操作微信与 hook 容器，
> 青龙必须拿到宿主 docker。数据卷要包含脚本目录，这样青龙读到的和 hook 挂载的是同一份。
> （镜像名以官方文档为准。）

访问 `http://<机器IP>:5700` 完成初始化。

> **本合集自身不需要额外准备什么**：
> - **不用装依赖** —— Python 侧只用标准库；JS 侧跑在 `woc-hook` 里用它自带的 node；
> - **不用配辅助脚本路径** —— `wxopen.py` / `wxcdp.py` 等都在仓库里，引擎每次跑会自动投递到微信容器；
> - **不用提供手机号** —— 不是会员时脚本会**自动注册**：按文案点掉品牌小程序自己弹的
>   注册表单（提交按钮通常写「确认授权开通并绑定会员」），手机号由小程序自己向微信取
>   （想少走几步可配 `WXSIGN_REGISTER_PHONE`，见 [第 5 节](#5-配青龙)）。

---

## 4. 挂 hook 容器（旁挂，不动云微的实例）

云微建的实例没有 `CAP_SYS_PTRACE`，frida attach 不上去，所以旁挂一个共享命名空间的 helper。

```bash
SCRIPTS=~/ql/data/scripts          # 与青龙数据卷同盘
REPO=~/wxsign                      # ← 改成你 clone 本仓库的实际路径
WX=$(docker ps --format '{{.Names}}' | grep '^woc-wx-' | head -1)   # 自动取实例名
VOL=woc-data-${WX#woc-wx-}          # 云微的数据卷名

# 把仓库里的 wxsign/ 整体拷进青龙脚本目录（保持 wxsign/ 这一层）
mkdir -p $SCRIPTS && cp -r $REPO/wxsign $SCRIPTS/

docker run -d --name woc-hook \
  --pid=container:$WX --network=container:$WX \
  --cap-add=SYS_PTRACE --security-opt seccomp=unconfined \
  -v $SCRIPTS:/work -v $VOL:/config:ro \
  node:22-slim sleep infinity
```

> `--pid` 与 `--network` **都必须是 `container:`**。只共享 PID 不共享网络的话，
> 小程序连不上 `ws://localhost:9421`（那是实例自己 netns 里的地址），hook 日志不会出现
> `miniapp client connected`。

装 WMPFDebugger 与 Linux 版 frida：

```bash
git clone --depth 1 https://github.com/evi0s/WMPFDebugger.git /tmp/wmpfd
docker exec woc-hook sh -c '
  mkdir -p /opt/wmpf && cd /opt/wmpf
  cp -r /tmp/wmpfd/src /tmp/wmpfd/package.json /tmp/wmpfd/tsconfig.json /tmp/wmpfd/frida /opt/wmpf/ 2>/dev/null || cp -r /tmp/wmpfd/* /opt/wmpf/
  cd /opt/wmpf
  npm i --ignore-scripts --registry=https://registry.npmmirror.com --no-audit --no-fund
  mkdir -p node_modules/frida/build
  curl -fsSL -o /tmp/f.tar.gz https://github.com/frida/frida/releases/download/17.18.0/frida-v17.18.0-napi-v8-linux-x64.tar.gz
  tar -xzf /tmp/f.tar.gz -C node_modules/frida/build --strip-components=1
  node -e "console.log(require(\"frida\").version)"
'
```

### 4.1 必须打的两个补丁

上游 WMPFDebugger 有两个坑会让本项目跑不起来，得补上：

```bash
bash wmpf/hook_patch.sh $WX      # 默认 hook 容器 woc-hook，幂等，可反复跑
```

它做两件事（各自都有备份与失败回滚）：

| 补丁 | 文件 | 不打会怎样 |
|---|---|---|
| ① 场景号白名单加 `1183` | `frida/hook.js` | 从「面板 → 搜索 → 结果卡片」打开小程序时场景号是 **1183**，不在上游白名单（1145/1256/1260…）→ 场景号不被改写成 1101 → 小程序**不会连** `ws://localhost:9421` → CDP 拿不到身份、换不了 token。现象：hook 日志里没有 `miniapp client connected` |
| ② 版本探测回退正则 | `src/platform/linux.ts` | 老版本（4.0.x 及更早）没有 `wmpf_release/<tag>_<x.y.z>` 串，上游取版本号会直接抛 `[frida] error in find wmpf version` 起不来 |

补丁 ① 顺带加了一行诊断日志：场景号不在白名单时打印 `[hook] scene NOT in whitelist: N`。
以后换入口或换微信版本又「换不到 token」，就把日志里那个 N 补进白名单。

> 固化下来（免得以后重建容器又要打一遍）：`docker commit woc-hook woc-hook:1`。

启动 hook：

```bash
docker exec -d woc-hook sh -c 'cd /opt/wmpf && node node_modules/ts-node/dist/bin.js src/index.ts > /tmp/wmpf.log 2>&1'
docker exec woc-hook tail -5 /tmp/wmpf.log     # 期望看到 [frida] script loaded, WMPF version: 25665
```

> 以后要重启它（换配置 / 微信版本后）：`bash wmpf/restart_hook.sh`。
> ⚠️ 它**只在启动时 attach 一次** —— 不重启的话，新拉起的小程序实例不会被挂上，
> 表现为 CDP 一个 context 都拿不到（`ctx=0`）。

### 4.2 企迈系的「首次手机号授权」（每个品牌跑一次）

**只对 `engine=qmai` 的品牌需要**（`brands.json` 里 `呷哺呷哺`、`李先生牛肉面大王` 等；
吾享 / 易东 / 微租林都不用 —— 它们的接口压根没有授权弹窗）。

企迈的签到要求**已绑手机号的会员**。绑定本身是**一次性**的（企迈服务端绑 openid、
**永久有效**），所以只有**接入该品牌的第一次**要过一遍 —— 跑完这条命令，之后每天的
定时任务就是全自动的，跟吾享系一样。

```bash
# 先确保只开目标小程序（渲染层拿不到 appId，多开时会读错页面 —— 见 README 第七节第 3 条）
docker exec -e DISPLAY=:1 $WX python3 /tmp/wxclean.py          # 清残留
docker cp wxsign/wxqm_auth.py $WX:/tmp/ && docker cp wxsign/wxdom.py $WX:/tmp/
docker cp wxsign/wxreg.py     $WX:/tmp/ && docker cp wxsign/wxwin.py $WX:/tmp/
docker cp wxsign/wxcdp.py     $WX:/tmp/ && docker cp wxsign/brands.json $WX:/tmp/

# 跑（--title 填该品牌的小程序窗口标题，也就是 brands.json 里的 miniapp）
docker exec -e DISPLAY=:1 $WX python3 /tmp/wxqm_auth.py --title 呷哺呷哺
docker exec -e DISPLAY=:1 $WX python3 /tmp/wxqm_auth.py --title 呷哺呷哺 --dry-run  # 只弹授权层，不点
```

退出码：`0` = 走完（含「已授权过、微信静默跳过」）；`3` = 没找到授权层 / 页面不对；
`4` = 连不上渲染层（hook 不通或小程序没开）。

> 脚本**零硬编码坐标、零颜色判断**：授权层是小程序自己的**普通 view 组件**
> （源码 `onAuthorization(){ this.selectComponent("#authorization").show() }`，
> 渲染成 `<wx-std-authorization id="authorization">`），所以**按选择器 + 文案**定位即可
> —— 勾选走 `.i-circle`，主按钮走「手机号一键登录」文案。
> 只有微信**原生**「允许」框在小程序 DOM 之外、走像素（判据是「白卡里的**绿色横向主段**」），
> 且**坐标系一律问页面自己**（CDP 的 `window.screenX/screenY`）——
> 所以窗口改成**侧边栏 / 分栏**形态也照样成立（见 README 第五节）。
>
> ⚠️⚠️ **排查「找不到授权层」时先看这一条**：同一个页面会有**多个渲染面**，
> 而 `wxdom` 默认挑「元素最多的那个」→ 会挑到**底层页面**，在里面永远找不到授权层。
> 实测呷哺签到页弹授权层时，`ctx=6`（302 节点，底层页）与 `ctx=9`（221 节点，
> 授权层在这）**url 和 title 完全一样**。
> 诊断工具：`docker cp survey/diagnostics_auth_ctx.py survey/dump_auth_tree.py $WX:/tmp/`，
> 然后 `docker exec -e DISPLAY=:1 $WX python3 /tmp/diagnostics_auth_ctx.py --tree`
> —— 它会直接告诉你「授权层在哪个 ctx」并把结构倒出来。
>
> ⚠️ 另：授权层**关闭后节点仍留在 DOM 里**（只是矩形塌成 0），
> 所以「选择器还在不在」不能当「授权层有没有弹出」的判据，要用可见性判断
> （脚本内部用 `find_ctx_with(..., visible=True)`）。
>
> ⚠️ 别照搬上面的文件名清单就以为万事大吉 —— 引擎平时跑签到时是**自动投递** helper 的
> （`ensure_helpers()`），但 `wxqm_auth.py` 是**你手动跑**的，所以要自己 `docker cp`。
> 更省事的做法：`docker cp wxsign/. $WX:/tmp/`（整目录拷过去）。

**没跑过的症状**：日常签到打印 `RESULT <slug> code=NOIDENT msg=企迈会话失效/未登录`。

> 另有两个企迈专属退出码（都来自 `wxsign.py::do_sign_qm`）：
> `NOACTID` = `brands.json` 里缺 `qm_activity`（商户级签到活动 ID，取法见该字段的注释）；
> `NOIDENT` = 拿不到企迈登录态（小程序没开 / hook 不通）。

---

## 5. 配青龙

在青龙后台点 **「环境变量」→ 新建**，逐个添加：

| 变量名 | 填什么 |
|---|---|
| `WOC_INSTANCE` | 微信实例容器名。在**宿主机**跑 `docker ps --format '{{.Names}}'` 查，形如 `woc-wx-2ada0225ca` |
| `WOC_HOOK` | hook 容器名，默认 `woc-hook` |
| `WXSIGN_PYTHON` | `/usr/bin/python3`（青龙自带的） |
| `WXSIGN_HOME_CONTAINER` | 脚本在**青龙容器里**的路径，即 `/ql/data/scripts/wxsign` |
| `WXSIGN_REGISTER_PHONE` | 可选。配了走 API 直连注册；不配则由脚本自动点掉小程序的注册表单（不需要手机号） |
| `WXSIGN_APPS` | 可选。只签这几个，逗号分隔的 slug 多选（如 `lakeke,laicai`）。**不配 = all** |
| `WXSIGN_EXCLUDE` | 可选。永不签这几个，逗号分隔的多选（如 `jiucun`）。**优先级最高** |

`WXSIGN_APPS` / `WXSIGN_EXCLUDE` 填的是 **slug**（`wxsign.py --list` 第一列）：
`lakeke` 辣可可 · `laicai` 来菜 · `jiucun` 九村烤脑花 · `jiuzhujianghu` 酒煮江湖。

- 白名单收窄范围，黑名单从范围里剔人 —— 两个可以同时用。
- 黑名单**盖过白名单和 `--all`**：`WXSIGN_APPS` 里写了、或者命令行加了 `--all`，照样排除掉。
  所以「这个号我永远不签」只写一条就够。
- 写 `all` 或不配，都等于不限制。
- slug 拼错不会静默忽略，日志里会打一行 ⚠️ —— 免得「配了却什么都没跑」查半天。
- 想看当前到底会签哪些：`bash /ql/data/scripts/wxsign/sign.sh --list`。

> 本合集**不需要** `WXSIGN_MINIAPP_PY` —— 打开小程序用的 `wxopen.py` 就在仓库里，
> 引擎每次跑会自动投递到微信容器（`ensure_helpers()`）。

### 定时任务

青龙后台点 **「定时任务」→ 新建**，命令填下表的，日程填 Cron：

| 名称 | 命令 | 定时 |
|---|---|---|
| 小程序签到合集 | `bash /ql/data/scripts/wxsign/sign.sh` | `10 8 * * *` |
| 保活（可选） | `bash /ql/data/scripts/wxsign/sign.sh --ensure-only` | `0 */2 * * *` |

> `sign.sh` 把参数**原样透传**给引擎，所以想临时换范围不用改环境变量：
> `bash .../sign.sh --apps lakeke,laicai` / `--exclude jiucun` / `lakeke` 都能用。
> 影响范围的口径见[上面那张表](#5-配青龙)的 `WXSIGN_APPS` / `WXSIGN_EXCLUDE`。

> 「保活」只保**进程活着**，**保不了登录态** —— 微信一掉登录就得人拿手机确认（[第 6 节](#6-微信侧常见故障这部分是运维的大头) 开头）。
> 掉登录时 `sign.sh` 会以退出码 2 / 3 中止，不会假装成功。

先「运行一次」验证，再看任务日志。每个品牌会打印一行 `RESULT <slug> code=... msg=...`。

---

## 6. 微信侧常见故障（这部分是运维的大头）

### ⚠️ 先讲清「登录」这件事（一次性配置，但要留意别让它掉）

**Linux 版微信点完「登录」按钮，必须在手机上确认才算登录成功**，没有例外。
Windows 版微信有「登录免确认」选项（可做到纯自动），**Linux 版客户端没提供这个选项**
（不是藏在设置里，是根本没做）。

但这**不影响「无人值守」这个定位**：登录属于**配置阶段的一次性动作**，跟配环境变量、
挂数据卷是一类事 —— 首次扫码登录一次（任何方案都躲不开这一步），之后只要**不重启**
就一直有效，运行期不需要任何人工。`sign.sh` 挂上定时任务之后就是全自动的。

唯一要留意的是**别让它掉登录**：

1. **掉登录 = 必须有人拿手机**（这一步替代不了）。定时任务在这一点上没法自愈 ——
   `sign.sh` 会在这种时候**明确报出来并中止**（退出码 2 / 3），不会假装成功往下跑。
2. **拖久了会升级成扫码**。手机长时间不确认，微信会提示「登录状态已过期」，
   之后就**只能扫码**了；而扫码必须能看到容器画面（KasmVNC 网页，见 [第 2.2 节](#22-建实例并登录)）。
3. 所以运维要点就一条：**容器和宿主都别重启**，也别让看门狗因内存把实例「柔和重启」掉
   （这就是 [第 2.1 节](#21-起面板与实例) 里把 `WOC_INSTANCE_MEM_SOFT_MB` 调到 2800 的原因）。

> 注意 `sign.sh --ensure-only` 那个「保活」任务只保**进程活着**，保不了**登录态** ——
> 它的用途是别让 hook / 面板挂掉，不是"替你维持登录"。

| 现象 | 原因与处理 |
|---|---|
| 界面停在登录页 | 实例被重启过。投两个脚本再跑：`docker cp wxsign/wxopen.py wxsign/wxlogin.py $WX:/tmp/ && docker exec -e DISPLAY=:1 $WX python3 /tmp/wxlogin.py`。它按**微信绿按钮的像素质心**定位「登录」并点，然后**等你手机确认**（默认最多 300s，`--wait N` 可改），确认成功才把主窗口拉成 1280×1024 全屏。⚠️ **不是免确认**：人不在就超时（rc=3）；拖到过期会出二维码（rc=2，需扫码） |
| 登录后主窗口缩成 280×380 停在 (372,194) | 登录流程没走完的症状（确认还没到）。这个状态下侧边栏像素扫描会全空，`open_panel` 报「按钮没找全」 |
| 点击全落空 / 面板认不出来 | 主窗口没全屏（比例坐标失效），或**有残留的小程序窗口盖在上面**。`wxclean.py` 会清残留：先找微信绿药丸点左边的「拒绝」遣散隐私弹窗，再走三道防线关窗 |
| 换不到 token，日志里 `invalid code` | 小程序窗口是**上一次留下的旧上下文**，里面的登录会话已失效。脚本会自动「关掉重开再试一次」 |
| 小程序窗口关不掉 | 上面压着隐私弹窗/悬浮提示，或渲染进程挂了。`wxclean.py` 会尽力；实在不行用下面的「硬清」 |
| 残留窗口越积越多 | 正常批量跑不会积（每个品牌跑完自己关）。只有反复手工测试才会 |
| 出口偶发 502 | 网络层瞬时错误，`api_post` 已内置重试 |

### 硬清：杀小程序运行时（比重启微信轻得多）

关闭按钮点不掉的卡死窗口，**杀 `WeChatAppEx` 进程**就能清掉 ——
它是小程序的运行时宿主，**杀掉不影响微信主进程、也不影响登录态**（实测）：
主进程 `wechat` 会按需拉起新的 `WeChatAppEx`。这比「重启微信」好得多，后者会掉登录。

```bash
# 容器内执行
docker exec $WX sh -c 'for p in $(ls /proc | grep -E "^[0-9]+$"); do \
  [ "$(cat /proc/$p/comm 2>/dev/null)" = "WeChatAppEx" ] && kill -9 $p; done'
```

工具里也带了：`wxclean.py --restart-runtime`（常规手段失败时才动手）。

> ⚠️ 杀完**要重启 hook 里的 WMPFDebugger** —— 它只在启动时 attach 一次：
> ```bash
> docker exec woc-hook sh -c 'for p in $(ls /proc | grep -E "^[0-9]+$"); do \
>   [ "$(cat /proc/$p/comm 2>/dev/null)" = "node" ] && kill $p; done'
> docker exec -d woc-hook sh -c 'cd /opt/wmpf && node node_modules/ts-node/dist/bin.js src/index.ts > /tmp/wmpf.log 2>&1'
> docker exec woc-hook tail -3 /tmp/wmpf.log   # 期望 [frida] script loaded, WMPF version: 25665
> ```

> **绝不要用 `xdotool windowclose` 关微信的窗口**：X 窗口销毁了，但微信内部「面板/小程序已打开」
> 的状态不复位 → 之后点侧边栏变成「切换关闭」，面板再也召不回来，只能重启微信。

### 8.x 微信实例容器重启之后（掉登录 + hook 必须重建）

微信实例容器**任何时候重启**（`docker restart`、Docker 服务重启、宿主重启），都会带来两件事：

**① 掉登录** —— 重启后停在登录页（280×380 的小窗，账号名 + 绿色「登录」）。
**这个登录窗不吃合成点击**：`xdotool mousemove --sync` 能把指针放到按钮正中心、
`click 1` 也返回成功，但界面**纹丝不动**。只能让用户在网页控制台（`http://127.0.0.1:36080`）
用真鼠标点，然后手机确认；或点「切换账号」扫码。

**② `woc-hook` **无法 `docker start`**，只能重建。**
hook 是用 `--pid=container:<wx>` + `--network=container:<wx>` 建的，Docker 把这两个值
**解析成 wx 容器的 ID** 固化了下来；wx 一重启 ID 就变了，于是：

```
Error response from daemon: joining network namespace of container:
  No such container: 5fab5474e45201a6cfdacc1a68c2c87f0f28d1d8ed7ab96d85253e5d1a249999
```

重建（**必须用固化过的镜像** `woc-hook:1`，裸 `node:22-slim` 会丢掉 WMPFDebugger 和 frida）：

```bash
WX=$(docker ps --format '{{.Names}}' | grep '^woc-wx-' | head -1)
VOL=woc-data-${WX#woc-wx-}
docker rm -f woc-hook
docker run -d --name woc-hook --pid=container:$WX --network=container:$WX \
  --cap-add=SYS_PTRACE --security-opt seccomp=unconfined \
  -v $SCRIPTS:/work -v $VOL:/config:ro woc-hook:1 sleep infinity
docker start woc-hook 2>/dev/null; docker exec -d woc-hook sh -c \
  'cd /opt/wmpf && node node_modules/ts-node/dist/bin.js src/index.ts > /tmp/wmpf.log 2>&1'
docker exec woc-hook grep -m1 'script loaded' /tmp/wmpf.log
```

> ⚠️ **重建后一定先验场景号补丁在不在**（这一步救过一次整批扫描）：
> ```bash
> docker exec woc-hook grep -c 1183 /opt/wmpf/frida/hook.js     # 必须是 1
> ```
> 不在就跑 `bash wmpf/hook_patch.sh woc-hook` 打上，然后**重新固化**：
> `docker commit woc-hook woc-hook:1`。
> 症状很隐蔽：小程序**能开**、窗口标题也对，但 CDP 里 `[enum] 有 wx 的上下文=[]`
> —— 因为从面板搜索打开的小程序场景号是 1183，不在白名单里就不会被改写成 1101，
> 也就不连 `ws://localhost:9421`。hook 日志里同样没有 `miniapp client connected`。
> 关窗一律点微信自己的关闭按钮。

---

## 7. 排查表

| 现象 | 原因 |
|---|---|
| `docker: not found` | 青龙容器没挂 docker 二进制，或没挂 `docker.sock` |
| `Cannot connect to the Docker daemon` | 同上 |
| 「微信实例容器未运行」 | 云微实例没起，或 `WOC_INSTANCE` 写错 |
| hook 日志没有 `script loaded` | WMPFDebugger 没起，或 WMPF 版本没有对应偏移配置 → `bash wmpf/check_wmpf.sh $WX` |
| hook 日志没有 `miniapp client connected` | 场景号没进白名单 → `bash wmpf/hook_patch.sh $WX`（见 [第 4.1 节](#41-必须打的两个补丁)） |
| CDP 一个 context 都读不到（`ctx=0`） | hook 只在启动时 attach 一次：重启过 hook、或杀过 `WeChatAppEx` 之后没重开小程序 → `bash wmpf/restart_hook.sh`，**再把小程序重开一次** |
| 一直「小程序没开成」 | 微信没登录，或 `brands.json` 里 appId / miniapp 名写错 |
| 日志里「点侧边栏『小程序』按钮 y=…」点了两次都「没能确认小程序面板」，整批都这样 | **多半是微信弹着模态框**（最常见是「退出登录？确定/取消」）—— 模态框会吞掉之后所有点击，症状伪装成「面板打不开」。脚本现在每轮都会自动遣散（`clear_modals`），若仍复现就截个图看：`docker exec -e DISPLAY=:1 $WX ffmpeg -f x11grab -video_size 1280x1024 -i :1 -frames:v 1 /tmp/s.png` |
| 侧边栏堆着「华夏家博 / 永伟美发店 / 媚姐养生会所」这类窗口，关不掉 | 那是微信小程序面板里的**推广位**，常规关窗（点关闭按钮）对它无效，攒到五六个就把侧边栏堵死 → 硬清：`docker exec -e DISPLAY=:1 $WX python3 /tmp/wxclean.py --restart-runtime`，**然后必须** `bash wmpf/restart_hook.sh`（引擎现在也会自己这么做） |
| `RESULT code=208/211` | token 失效 —— 重跑一次即可（脚本会自己刷） |
| `RESULT code=401` | 该账号还不是这个品牌的会员 → 脚本会自动注册；若仍失败，看 `[ui]` 日志与 `shots/<slug>/` 截图 |
| `RESULT code=-1` | 网络层错误（已重试仍失败），看 msg |
| 换了微信账号后「抓不到身份 / invalid code」，换回旧号又好了 | env 里还粘着旧账号的 `WX_OPENID` → `python3 wxsign.py --all --reset-identity`（见 [第 8.3 节](#83-换微信账号小号验证完--换大号)） |
| 「新建实例」看着是干净的，但旧微信还在 | 删实例时没勾「彻底清除」，数据卷被保留了 → 见 [第 8.2 节](#82-装过云微想彻底卸干净) |

---

## 8. 后续运维：多开 / 卸干净 / 换微信账号

首次部署完之后，这三件事迟早会遇到。

### 8.1 再加一个微信实例（多开）

云微本身就是多实例设计：面板「实例」页 →「**新建实例**」→ 选类型（微信 / Chromium）、命名 →
面板自动 `docker run` 起一个新容器。每个实例是**独立的一整套**：

```
容器     woc-wx-<hash>          实测：woc-wx-2ada0225ca
数据卷   woc-data-<hash>        实测：woc-data-2ada0225ca   ← 微信本体 + 登录态都在这里
登录     要再扫一次码（实例之间互不影响）
网络     woc-net                面板与实例之间的内网（多个实例共用）
```

⚠️ **本项目的脚本一次只操作一个实例** —— 它只读 `WOC_INSTANCE` 这一个变量。
所以「同时跑两个微信账号」= 跑两份，各含一套环境变量与脚本目录：

```bash
# 第二份（比如大号）指向另一个实例
WXSIGN_HOME=/ql/data/scripts/wxsign-b \
WXSIGN_HOME_CONTAINER=/ql/data/scripts/wxsign-b \
WOC_INSTANCE=woc-wx-<另一个 hash> \
python3 /ql/data/scripts/wxsign-b/wxsign.py --all --ensure
```

两边的 `brands/` 目录是分开的，凭证不会串。

### 8.2 装过云微、想彻底卸干净

**最常见的「没卸干净」是删实例时没勾「彻底清除」** —— 云微默认**保留数据卷**
（有意为之：删了实例还能重建回来），于是微信本体、登录态、几百 MB 都还留在盘上。

先看还留着什么（命名规律是本机实测的）：

```bash
docker ps -a --format '{{.Names}}\t{{.Status}}' | grep -E 'woc-|wechat'
docker volume ls --format '{{.Name}}' | grep woc
docker network ls --format '{{.Name}}' | grep woc
```

再按顺序删：

```bash
docker rm -f woc-hook                 # 旁挂的 hook（如果有）
docker rm -f woc-wx-<hash>            # 微信实例；hash 从上面第一条命令看
docker rm -f woc-panel                # 面板放最后（它管着实例）

docker volume rm woc-data-<hash>      # ⚠️ 微信本体 + 登录态都在这，删了要重装 + 重新扫码
docker network rm woc-net             # 面板与实例之间的内网

rm -rf ~/woc/data-panel               # 面板数据（账号、实例记录、日志）
rm -rf ~/woc                          # 如果你当初就是在 ~/woc 里建的
```

> **只删容器不删卷**：下次「新建实例」看着是干净的，其实旧微信还在卷里 ——
> 省下一次 200 MB 下载，但登录态也可能跟着回来。想真正从零开始，务必把卷删掉。
> 反过来：**只是换微信版本 / 重装微信时别删卷**，登录态在 `/config/.xwechat`，删了就得多扫一次码。

端口起不来时（重建面板）：`docker ps -a | grep 36080`，把占着 `36080` 的旧容器删掉。

### 8.3 换微信账号（小号验证完 → 换大号）

**只换微信登录是不够的。** 脚本侧还存着旧账号的一整套凭证，不清掉会一直失败 ——
而且症状很难联想到换号：

```
换了新账号后，日志报「抓不到身份 / invalid code」；换回旧账号又好了
```

原因是 `wxrefresh.js` 会读 env 里的 `WX_OPENID` 当「目标上下文」去挑 CDP 连接，
写回身份时又带「env 里已有就不覆盖」的守卫 → **旧 openId 一直粘着**。

三步：

```bash
# 1) 让微信回登录页，用新账号扫码
#    最省心：重启一次实例（微信会回到登录页，见第 6 节的登录说明），然后扫码
#    —— 扫码要看容器画面（KasmVNC 网页）

# 2) 清掉脚本侧的旧账号凭证（会打印清哪些；加 --dry-run 可先看，不写文件）
python3 wxsign.py --all --reset-identity

# 3) 跑一次：自动重抓身份，并在每个品牌自动注册（新账号在各家都还不是会员）
python3 wxsign.py --all --ensure
```

`--reset-identity` 清这 8 个（有才清）：
`WX_OPENID`、`WX_UNIONID`、`WX_TOKEN`、`WX_GCID`、`WX_MEMBERID`、`WX_CARDID`、`WX_CARDNO`、`WX_THIRDSHOPID`。

保留与账号无关的：`WX_MPID`（品牌侧 id，同品牌所有人一样）、`WX_APPID`、`WX_GAMEID`（活动 id，公开常量）、`WX_REGISTER_*`（配置项）。

换号后要知道的三件事：

- **会重新注册一遍**：会员是绑 openId 的，新账号在每家都还不是会员 → 脚本自动注册；
  卡号、积分从零开始。
- **旧账号的会员记录不会消失**（那是服务商侧的），只是脚本不再用它。
- **想两个号都长期在跑**：别在同一个实例里来回切 —— 按 [8.1](#81-再加一个微信实例多开)
  新开一个实例，两个账号的设备指纹完全隔离，也不用反复清凭证。

> 同一台设备频繁退出 / 换号本身就是个风控信号，尽量一次换定。
