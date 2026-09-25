# 企迈（qmai）后端接口逆向与实测记录

> 目标：判断「企迈系小程序」能不能接入本合集做每日签到。
>
> **结论先说**：接口完全可用、适配器已跑通；但实测的**两个商户都没开签到活动** ——
> 服务端回 `400042 商家未开启此功能`，而且**小程序自己也签不了**。

---

## 一、结论

| 项 | 结果 |
|---|---|
| 接口链路 | ✅ 完全打通（鉴权 / 路径 / header / 响应格式全部实测确认） |
| 适配器 | ✅ 已落地：`wxqm.py`（容器内取登录态）+ `wxsign.py` 的 `do_sign_qm()`，`engine: "qmai"` |
| 呷哺呷哺（storeId `214176`） | ⛔ `400042 商家未开启此功能` |
| 李先生牛肉面大王（storeId `49112`） | ⛔ 同上 |
| 签到活动 | ❌ 两家的**规则都配了、活动没发布** |

**关键证据（实测，不是推断）**：用 CDP 抓**小程序自己发的**那个 `sign/detail` 请求 ——
同 URL、同 body、带它自己的 token —— 响应与我们复刻的一字不差：

```json
{"status":false,"code":"400042","message":"商家未开启此功能","data":null}
```

而同一时刻 `sign/rule` 能返回**该商户的完整奖励配置**：

```json
{"status":true,"code":"0","data":[{"activityContent":"<p>活动规则：群粉每天签到一次…",
 "detailInfo":{"signInCycle":0,"signInBaseRewards":0,"signInCalculationRole":2,
 "signInRoleList":[{"presentIntegral":1,"signInDays":1},{"presentIntegral":1,"signInDays":7},
 {"presentIntegral":1,"signInDays":19},{"presentIntegral":1,"signInDays":30}]}}]}
```

签到页的页面数据也是空的：`activityId:""`、`isLoad:false`、`signDays:[]` ——
界面上就是空白的，**没有可点的签到按钮**。

> 交叉验证过三遍：**容器内 curl / 宿主机 curl / 小程序自己的 wx.request**，
> 三条路的响应完全一致。

---

## 二、接口地图

- **域名**：`https://webapi.qmai.cn`（`env=master` 时）。域名是「子域名表 + 环境后缀」
  拼出来的，包里那张写满完整 URL 的配置表**不参与**拼 URL。
- **路径前缀**：`/web/<biz>/...`
  - `biz=catering` ← 餐饮大盘（李先生用的就是这个）
  - `biz=mealmate-apiserver` ← 呷哺系
  - 实测两条前缀**都能通**
- **认证**：两个请求头，**没有签名**
  - `store-id: <商户号>`（= 包内 `bid` = storage `loginData.store.id`）
  - `Qm-User-Token: <会话>`（= storage `loginData.token`）
- **业务头**：`Accept: v=1.0`、`Qm-From: wechat`、`Qm-From-Type: <biz>`、`scene: 1101`

| 接口 | 作用 |
|---|---|
| `POST /web/<biz>/integral/sign/detail` | 签到详情 —— **权威判据**，body `{"appid"}` |
| `POST /web/<biz>/integral/sign/rule` | 规则文案 + 奖励档位（**不需要活动已发布**） |
| `POST /web/<biz>/integral/sign/signIn` | 签到，body `{"appid", "activityId"}` |
| `POST /web/<biz>/crm/total-points` | 积分余额 |
| `POST /web/<biz>/shop/catering-shop-list` | 门店列表（返回 `brandId` + 664 家门店） |

**响应格式**（源码 `M()` 的 `success` 回调就是这么解的）：

```json
{"status": true, "code": "0", "message": "ok", "data": {...}, "trace_id": "..."}
```

---

## 三、错误码

| code | 含义 | 备注 |
|---|---|---|
| `0` / `status:true` | 成功 | |
| `10008` | 用户未登录 | 没带 token |
| `9001` | 登录超时 | token 无效 / 过期 |
| **`400042`** | **商家未开启此功能** | 过了鉴权、走到业务层才报 —— **本次的核心结论就是它** |
| `20013` | 活动ID为空 | `activityId` 没下发，与 400042 同源 |
| `41000` | 请求异常 | `store-id` 无效 |

> **`400042` 与「未登录」的区别很关键**：不带 token → `10008 用户未登录`；
> 假 token → `9001 登录超时`；**只有带有效 token 才会走到 `400042`**。
> 这说明鉴权是好的、我们的会话是有效的 —— 排除掉「登录问题」这条岔路。

---

## 四、身份从哪来

企迈是**静默登录**：小程序打开时自己用 `wx.login` 换一次会话，结果写进逻辑层 storage：

```json
loginData = {
  "token": "S9mlWSt6xv0ypK4oJYN-uo9e…",              ← 就是 Qm-User-Token
  "store": {"id":"49112","name":"李先生牛肉面大王","store_type":66},
  "user":  {"eOpenId":"f4OGxmxz…","eMobile":null}   ← 手机号尚未绑定
}
```

`wxqm.py` 就是读这个 —— **不需要我们自己调登录接口**。
（对照：易东要 `wx.login` 的 code，微租林要 code，**企迈只要现成的会话**。）

---

## 五、踩过的坑（都值得记）

1. ⚠️ **路径漏了 `/web` 前缀 → 阿里云 WAF 的 JS 挑战页**。
   阿里云 WAF 对「不存在的路由」会甩回一个 **110310 字节**的挑战页
   （响应头 `Punish-Loc: keepper`，body 是 `aliyun_waf_*` + 混淆 JS）。
   当时把它误判成「被墙了」，还写了 WAF 求解器、试过 jsdom / Chrome / 换宿主机……
   **全是弯路，验证的其实是自己的错误**。
   → **教训：响应形态异常时，先怀疑自己请求的 URL 对不对，再怀疑防护。**
2. ⚠️ **本机代理会返回别的请求的缓存**。第一次打 `webapi.qmai.cn`，响应头里赫然写着
   `Access-Control-Allow-Origin: http://zhyx.eingdong.com` 和易东的 `sessionKey` cookie ——
   那是之前易东请求的缓存，**差点据此得出「这域名返回易东数据」的荒谬结论**。
   → curl 一律带随机参数（`?t=<ts>`）+ `Cache-Control: no-cache`。
3. ⚠️ **JS 层拦不到企迈的请求**：它自己的模块加载期就捕获了 `wx.request` 的引用
   （`n || (n = e[t])`），事后替换无效；SDK 实例藏在模块闭包里也扫不到。
   → **正解是用 CDP 的 Network domain 抓**（`survey/host_net.py`）——
     就是它把整件事揭穿的。
4. ⚠️ **接口要求大小写不敏感地拼 header 还不够**，`store-id` 与 `Qm-User-Token`
   必须**同时**带上：缺 store-id 会走默认商户，缺 token 直接 `10008`。

---

## 六、如果哪天要再验（换个开了活动的商户）

```
1. 打开目标小程序（先过隐私页 —— 它的隐私页有 confirm() 方法，可以直接调）
2. 读 storage 的 loginData（wxqm.py）→ token + storeId
3. 打 /web/<biz>/integral/sign/detail
   · status:true        → 活动开着，可以签到
   · 400042             → 商户没开，别折腾了
4. 活动开着的话，把 brands.json 里那条的 enabled 改成 true 即可
```

`brands.json` 里已有企迈条目（`lixiansheng`），字段就两个：
`engine: "qmai"` + `qm_biz: "catering"`（缺省 `catering`，呷哺系是 `mealmate`）。

---

## 七、相关工具（`survey/` 目录）

| 文件 | 用途 |
|---|---|
| `host_eval.py` | 通过 CDP 在小程序逻辑层执行任意 JS |
| `host_net.py` | **通过 CDP 抓小程序真实请求（含响应体）** —— 揭穿 `/web` 缺前缀的就是它 |
| `waf/` | 早期 WAF 实验的存档（已证明是弯路，留作反例） |
