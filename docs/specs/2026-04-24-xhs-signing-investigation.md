# XHS Web API 签名逆向探索纪要

**日期**: 2026-04-24
**状态**: 未决 — 等待架构方案选择（A / B / C 见下）
**前置**: Plan 1（平台抽象）+ Plan 2 阶段 A（XHS 基础设施）全部完成

## 背景

完成 Plan 2 阶段 A 后进入 Plan 3（XHS 真实 API 接入）。原计划用 `xhshow` 生成 `x-s / x-s-common / x-t / x-b3-traceid / x-xray-traceid` 五件套签名 header，调 `/api/sns/web/v1/user_posted` 下载用户主页笔记。实测失败。

测试目标：秃头金金主页 `https://xhslink.com/m/5kcCust1t6Z` → 解析得 `user_id=55c726695894464ef542aea0, xsec_token=YBbTU9A0gqz095TGQUh16x7JntBoAaPXp-zQk8hjrx768=, xsec_source=app_share`。

## 事实清单

- ✅ Cookie 有效：`/api/sns/web/v2/user/me` 返回 200, success=True, 显示当前账号（`5d195614000000001601d34b` / nickname=啊哦）
- ✅ Cookie 格式完整：包含 `a1`, `web_session`, `webId`, `websectiga`, `xsecappid`, `gid` 等
- ✅ 浏览器自己访问主页：XHS 前端 JS 的 `user_posted` 请求返回 200 success=True，拿到 30 条笔记
- ❌ 用 xhshow 0.1.9 签名发 HTTP 请求：406, code=-1（签名被 Kong 网关拒绝）
- ❌ 调浏览器里 `window._webmsxyw(path)` 生成签名再 fetch：HTTP 200, code=300011 "当前账号存在异常"
- ❌ 在已登录 Playwright 页面上下文中 `fetch()` 手动构造请求：同样 300011

## 关键发现

1. **xhshow 0.1.9 的签名与 XHS 服务端当前期望算法不匹配**
   - xhshow 的 `x-s` 前缀是 `XYS_`（这个前缀 XHS 确实也还在用）
   - 但 xhshow 内部按旧参数映射计算出的字节序和 XHS 期望值不同
   - 网关 Kong 识别为非法签名 → 406

2. **浏览器 `_webmsxyw` 返回新版 `XYW_` 格式**
   - `_webmsxyw(path)` 返回 `{X-s: "XYW_eyJzaWduU3ZuIjoiNTYiLCJzaWduVHlwZSI6IngyIiw...", X-t: 1777...}`
   - 解出来是 `{"signSvn":"56","signType":"x2","appId":"xhs-pc-web","signVersion":"1","payload":"..."}`（x2 算法）
   - 但 XHS 自己发网络请求时 header 里仍是 `XYS_` 前缀 —— 说明**JS 内部还有一层把 `XYW_` payload 转换成 `XYS_` 签名的 wrapper**
   - 同时这层 wrapper 会额外产出 `x-s-common`

3. **还有风控字段 `x-rap-param`**
   - XHS 网络请求里有一个 base64 的 `x-rap-param: ByQBBgAAAAEAAAAUAAAAxFWautY...`
   - 大概率由 `window.xhsFingerprintV3` 对象生成（浏览器指纹）
   - 缺这个不一定立刻拒，但可能导致账号被风控系统打标（推测 code=300011 部分原因）

4. **整个签名流程都被 `_ace_2267` / `_ace_c42c` / `_ace_831d` / `_ace_992e7` 闭包包裹**
   - 动态变量名 + 控制流扁平化，不能直接定位 wrapper 函数
   - 要完整还原需要 JS 反混淆 + 单步跟踪，工作量 2-5 天

## 已安装依赖（dev 环境）

```bash
pip install zxing-cpp qrcode pillow 'httpx[http2]' xhshow playwright
playwright install chromium
```

`xhshow 0.1.9` 仍安装着，尽管不能直接用 —— 其实现可作为后续尝试的参考。

## 上游参考
- https://github.com/JoeanAmier/XHS-Downloader （新版也依赖 xhshow；看起来他们的 user_posted 使用其实也可能有同样问题）
- https://github.com/Cloxl/xhshow （签名库）
- 临时 clone 位置：`/tmp/XHS-Downloader`（会被系统清理，长期保留请重新 clone）

## 诊断脚本归档

以下临时脚本可作为后续调试起点：

| 路径 | 用途 |
|------|------|
| `/tmp/xhs_diag.py` | Playwright 访问 XHS + 首页截图 |
| `/tmp/xhs_diag2.py` | DOM 枚举找 QR img 元素 |
| `/tmp/xhs_diag3.py` | 从 `img.qrcode-img` data URL 读原始 PNG |

（临时文件可能丢失，重要脚本若后续要用需要移入仓库或重写。）

## 决策点（待用户选择）

### 方案 A：Playwright 作为数据源（推荐）

- 不自行签名；`XHSPlatformClient` 启动长驻 Playwright context，带入 cookie
- `fetch_list(user_id)`：`page.goto(profile_url)` → 监听 `user_posted` 响应事件 → 自动滚动触发分页 → 收集所有 notes
- `fetch_single(note_id)`：`page.goto(explore_url)` → 监听 `/feed` 响应
- 拿到 note JSON 后，图/视频 URL 走 CDN 不需签名，用现有 `DownloadEngine` 直接下
- ✅ 签名问题一劳永逸；XHS 升级自动跟上
- ✅ 请求行为=真浏览器，风控友好
- ⚠️ 长驻 ~200MB 浏览器进程
- ⚠️ 分页靠滚动，几秒/页（对单用户 100 条笔记够用）

### 方案 B：搁置 XHS 下载能力

- 保留 Plan 1/2 成果，XHS 下载先放下
- 等 xhshow 升级或等有人公开新签名算法
- ✅ 不再负担
- ❌ XHS 下载永远缺失

### 方案 C：继续逆向签名算法

- 深入 XHS 混淆 JS 里的 `x-s-common` 生成 + `xhsFingerprintV3` 指纹算法
- 估算 2-5 天深度工作
- ✅ 纯 HTTP 调用，轻量高并发
- ❌ XHS 每次升级前端都要跟进逆向

## 当前建议

方案 A —— 已有 Playwright 依赖（扫码工具已用过），长驻一个浏览器进程的成本对单用户场景可控；主要代价是代码要多维护一套"浏览器化数据源"模式。

但决策权在用户。
