# 部署：微信容器 + hook + 青龙

本合集跑起来要三样东西：**一个跑着微信的 Linux 容器**（签到脚本从它里面取登录态）、
**一个旁挂的 hook 容器**（读小程序的 JS 层，用来拿 jsCode 和 appId）、
**青龙面板**（定时调度）。

**硬性前提：三者必须在同一台宿主机上。** token 靠微信客户端当场产生，服务端没法自举。

资源参考：微信实例 1.2 GiB（跑起小程序约 1.8 GiB）+ hook 0.47 GiB + 面板 0.12 GiB + 青龙 0.2 GiB，
合计约 **2.5 GiB 内存、6 GB 磁盘**；建议 **2 核 4 GiB** 起。

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
WOC_PASSWORD=<上一步那串>
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
>   -e PANEL_ADMIN_USER=admin -e PANEL_ADMIN_PASSWORD=<同一个密码> \
>   -e WOC_SPOOF_OS=1 -e WOC_INSTANCE_MEM_SOFT_MB=2800 -e WOC_INSTANCE_MEM_HARD_MB=4000 \
>   -e TZ=Asia/Shanghai --restart unless-stopped gloridust/woc-panel:latest
> ```

浏览器打开 `http://<机器IP>:36080`（用户名 `admin`）→ 新建「微信实例」→ 等镜像拉完、
微信自动装好 → 进实例 → **手机扫码登录**（建议用闲置小号）。

> 首次必然是**扫码**（这时还没有登录态）。之后只要实例不重启就一直有效 ——
> 掉登录只发生在实例重启后，而重启后点「登录」**还得手机确认**（Linux 版没有免确认选项）。
> 详见 §6 开头的「登录这件事」。

**忘了面板密码**：

| 情况 | 做法 |
|---|---|
| 正常 | `grep WOC_PASSWORD ~/woc/.env` |
| `.env` 没了 | `docker inspect woc-panel --format '{{range .Config.Env}}{{println .}}{{end}}' \| grep PANEL_ADMIN` |
| 没设过 | 官方 compose 默认 `WOC_PASSWORD=wechat`。**别用默认值** —— 这面板能操作宿主 Docker |
| 想换 | 改 `~/woc/.env` 后重建面板；微信实例在数据卷里，不受影响 |

> 注意本方案有**两个 `.env`**，别搞混：`~/woc/.env` 是**云微面板**的；
> `brands/<slug>.env` 是**签到脚本**的。青龙环境变量页里配的是后者那类。

### 2.5 先验 WMPF 版本（不通过就别往下走）

hook 靠 frida 找 WMPF 里的偏移，**WMPF 版本必须落在 WMPFDebugger 的配置里**。
**要看的是 WMPF 版本，不是微信版本号**（多个微信版本可能共用同一个 `WeChatAppEx`）。

```bash
docker exec <实例容器名> sh -c 'ls /opt/wechat 2>/dev/null; \
  find / -name "WeChatAppEx" -maxdepth 6 2>/dev/null | head -3'
# 或者直接从 hook 启动日志看： [frida] script loaded, WMPF version: xxxxx
```

已知可用组合：微信 Linux **4.1.13.23** / WMPF **2.5.6.25665**（deb 231,359,624 字节，
sha256 `b7d0f8d53e9f648bc2c77a6096a04100d008f2d9f0d3988a2a4859b5992aca0a`）。
另外 4.1.13.9 与 4.1.13.23 的 `WeChatAppEx` 字节完全相同（都是 25665）。

> ⚠️ **别点面板里的「更新微信」** —— 它下的是不带版本号的直链，永远拿最新版；
> 一旦新版 WMPF 没有对应偏移配置，hook 就挂不上。
> 万一漂了：① 把最接近的配置改名试挂（同一 `x.y.z` 下不同 build 偏移可能一样）；
> ② 换一个微信 deb 版本（`/config/wechat` 做符号链接切版本，登录数据在 `/config/.xwechat`，不丢登录）。

---

### 2.6 让小程序以「手机竖版」运行（建议做）

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
docker exec <实例> sh -c 'cp /etc/xdg/openbox/rc.xml /config/.config/openbox/rc.xml.orig.bak'
# ② 改规则
docker exec <实例> sh -c 'sed -i "s|<application class=\"\*\"><maximized>yes</maximized></application>|<application name=\"微信*\"><maximized>yes</maximized></application>|" /etc/xdg/openbox/rc.xml'
# ③ 放到 HOME（= /config，挂载卷）优先位置，容器重建也不丢
docker exec <实例> sh -c 'cp /etc/xdg/openbox/rc.xml /config/.config/openbox/rc.xml'
# ④ 重载
docker exec <实例> sh -c 'DISPLAY=:1 openbox --reconfigure'
```

实测结果：小程序窗口变成 **410x776 @ (435,124)** 的手机竖版，页面按 410 CSS px 布局；
主窗口与面板仍是 1280x1024（它们的标题命中「微信*」）。

> ⚠️ `--reconfigure` **不会重排已经打开的窗口**。已有的小程序窗口要关掉重开才生效；
> 如果面板窗口是在改规则**之前**创建的，它会保持原样，但下次重开就会按新规则最大化。

**回滚**：

```bash
docker exec <实例> sh -c 'cp /config/.config/openbox/rc.xml.orig.bak /etc/xdg/openbox/rc.xml && cp /etc/xdg/openbox/rc.xml /config/.config/openbox/rc.xml && DISPLAY=:1 openbox --reconfigure'
```

#### 这对脚本意味着什么（重要）

小程序窗口不再铺满屏幕、**位置也不在 (0,0)** → 所有点击与截图都必须加**窗口原点**：

```
屏幕坐标 = 窗口原点 + 页面坐标
```

换算这么简单，是因为实测（用 CDP 问页面自己）：

```
window.screenX/screenY = (435,124)   ← 与 xdotool 报的窗口原点完全一致
innerHeight(779) > outerHeight(776)  ← 顶部那条「⌂ 首页 ●●● ─ ⊙」是**覆盖层**，不占视口
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

---

## 4. 挂 hook 容器（旁挂，不动云微的实例）

云微建的实例没有 `CAP_SYS_PTRACE`，frida attach 不上去，所以旁挂一个共享命名空间的 helper。

```bash
SCRIPTS=~/ql/data/scripts          # 与青龙数据卷同盘
WX=<实例容器名>                     # 例 woc-wx-2ada0225ca
VOL=woc-data-${WX#woc-wx-}          # 云微的数据卷名

# 把本仓库的 wxsign/ 放进青龙脚本目录（保持 wxsign/ 这一层）
mkdir -p $SCRIPTS && cp -r <本仓库>/wxsign $SCRIPTS/

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

```bash
# ① 场景号白名单：从「小程序面板 → 搜索 → 结果卡片」打开小程序时场景号是 1183，
#    不在上游白名单里 → 小程序不会连 ws://localhost:9421 → CDP 拿不到 appId、换不了 token。
#    现象：hook 日志里没有 miniapp client connected。
# ② 老版本（4.0.x 及更早）没有 wmpf_release/<tag>_<x.y.z> 串，上游取版本号会直接抛
#    [frida] error in find wmpf version 起不来 → 需要一条回退正则。
```

`wxsign.py` 不做这两个补丁（它不碰 WMPFDebugger 源码），请按上游 issue 的方式改
`frida/hook.js` 的 `sceneNumberArray`（加 `1183`，并加一行诊断日志）、
以及 `src/platform/linux.ts` 的版本探测。改完 `docker commit woc-hook woc-hook:1` 固化。

> 换入口或换微信版本后如果又「换不到 token」，用 `--debug-frida` 起一次 hook，
> 日志会打印 `[hook] scene NOT in whitelist: N`，把 N 补进白名单即可。

启动 hook：

```bash
docker exec -d woc-hook sh -c 'cd /opt/wmpf && node node_modules/ts-node/dist/bin.js src/index.ts > /tmp/wmpf.log 2>&1'
docker exec woc-hook tail -5 /tmp/wmpf.log     # 期望看到 [frida] script loaded, WMPF version: 25665
```

---

## 5. 配青龙

### 环境变量

```
WOC_INSTANCE=woc-wx-xxxxxxxx                    # 微信实例容器名
WOC_HOOK=woc-hook                               # hook 容器名
WXSIGN_PYTHON=/usr/bin/python3
WXSIGN_HOME_CONTAINER=/ql/data/scripts/wxsign   # 脚本在**容器里**的路径
WXSIGN_REGISTER_PHONE=13800000000               # 可选：不是会员时自动注册用
```

> 本合集**不需要** `WXSIGN_MINIAPP_PY` —— 打开小程序用的 `wxopen.py` 就在仓库里，
> 引擎每次跑会自动投递到微信容器（`ensure_helpers()`）。

### 定时任务

| 名称 | 命令 | 定时 |
|---|---|---|
| 小程序签到合集 | `bash /ql/data/scripts/wxsign/sign.sh` | `10 8 * * *` |
| 保活（可选） | `bash /ql/data/scripts/wxsign/sign.sh --ensure-only` | `0 */2 * * *` |

> 「保活」只保**进程活着**，**保不了登录态** —— 微信一掉登录就得人拿手机确认（§6 开头）。
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
   之后就**只能扫码**了；而扫码必须能看到容器画面（KasmVNC 网页，见 §2.4）。
3. 所以运维要点就一条：**容器和宿主都别重启**，也别让看门狗因内存把实例「柔和重启」掉
   （这就是 §2.3 里把 `WOC_INSTANCE_MEM_SOFT_MB` 调到 2800 的原因）。

> 注意 `sign.sh --ensure-only` 那个「保活」任务只保**进程活着**，保不了**登录态** ——
> 它的用途是别让 hook / 面板挂掉，不是"替你维持登录"。

| 现象 | 原因与处理 |
|---|---|
| 界面停在登录页 | 实例被重启过。投两个脚本再跑：`docker cp wxsign/wxopen.py wxsign/wxlogin.py <实例>:/tmp/ && docker exec -e DISPLAY=:1 <实例> python3 /tmp/wxlogin.py`。它按**微信绿按钮的像素质心**定位「登录」并点，然后**等你手机确认**（默认最多 300s，`--wait N` 可改），确认成功才把主窗口拉成 1280×1024 全屏。⚠️ **不是免确认**：人不在就超时（rc=3）；拖到过期会出二维码（rc=2，需扫码） |
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
docker exec <实例> sh -c 'for p in $(ls /proc | grep -E "^[0-9]+$"); do \
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
> 关窗一律点微信自己的关闭按钮。

---

## 7. 排查表

| 现象 | 原因 |
|---|---|
| `docker: not found` | 青龙容器没挂 docker 二进制，或没挂 `docker.sock` |
| `Cannot connect to the Docker daemon` | 同上 |
| 「微信实例容器未运行」 | 云微实例没起，或 `WOC_INSTANCE` 写错 |
| hook 日志没有 `script loaded` | WMPFDebugger 没起 / WMPF 版本没有对应偏移配置 |
| hook 日志没有 `miniapp client connected` | 场景号没进白名单（见 4.1） |
| 一直「小程序没开成」 | 微信没登录，或 `brands.json` 里 appId / miniapp 名写错 |
| `RESULT code=208/211` | token 失效 —— 重跑一次即可（脚本会自己刷） |
| `RESULT code=401` | 该账号还不是这个品牌的会员，且没配注册手机号 |
| `RESULT code=-1` | 网络层错误（已重试仍失败），看 msg |
