# jsdom TDC 全面重构 — 设计文档

- **日期**: 2026-04-19
- **作者**: gehonglu + Claude
- **状态**: 已批准，进入实施
- **关联 issue / 背景**: 最近一次 `crack-tcaptcha solve --appid 2046626712`（image_select）连续 3 次返回
  `errorCode=9`。底层使用 `ScraplingBrowserProvider`（真实 Chromium）仍失败，说明
  `errorCode=9` 不是 fingerprint/cookie 问题，而是服务端对 TDC `collect` 负载的**字段校验**
  不通过。

---

## 1. 目标与非目标

### 目标（必须达成）

- 让 `uv run crack-tcaptcha solve --appid 2046626712`（image_select, show_type=click_image_uncheck）
  在 5 次尝试内**至少 1 次**拿到非空 `ticket`。
- 保持 **slider** 回归：现有测试 appid 的通过率不低于重构前。
- 保持 **icon_click** 回归：现有测试 appid 的通过率不低于重构前。
- TDC 执行链路回到 **Node.js + jsdom** 动态方案，删除所有无头浏览器依赖。
- 后续 tdc.js 小幅变更不需要大调整（只改 profile/invariants 常量表）。

### 非目标（明确不做）

- ❌ flashTCaptcha 的静态 XTEA / opcode VM / 纯逆向路线。
- ❌ 任何无头浏览器（Scrapling / Playwright / Chromium）。
- ❌ 多 profile 随机选择（只实现 Chrome 131 Win 一个档案，YAGNI）。
- ❌ 改动 `pow.py` / `captcha_type.py` / solver / LLM。
- ❌ tokenid 的业务使用（当前 verify 不需要）。

---

## 2. 方案选型（A/B/C 回顾）

- **A（选中）**：jsdom 全面重构 — 事件驱动 + Profile 注入 + cd[] invariants 覆盖。
- B：jsdom 最小修复（只改 origin + 事件派发）。风险大、下次 tdc.js 变更就挂。
- C：jsdom + 静态 XTEA 后处理。偏向 flash 的纯逆向，不符合项目方向。

**选择 A 的理由**：
1. 贴合项目定位"动态、不依赖浏览器"。
2. flashTCaptcha 的 `collect/profile.py` / `tdc/invariants.py` / `collect/builder.py`
   正好是"可参考的参数/常量"来源。
3. `errorCode=9` 真浏览器都出，说明 tdc.js 看到的环境 vs 服务端期望的环境不匹配
   ——只有系统化 profile + invariants 对齐能根治。

---

## 3. 架构总览

### 模块布局

```
src/crack_tcaptcha/tdc/
├── __init__.py
├── provider.py                 # TDCProvider protocol（微调，新增 kind 语义说明）
├── nodejs_jsdom.py             # Python subprocess bridge（重写）
└── js/
    ├── package.json            # 只保留 jsdom 依赖
    ├── tdc_executor.js         # 入口，轻薄化（重写）
    ├── profile.js              # 新：Chrome 131 Win 档案
    ├── env_patch.js            # 新：navigator/screen/canvas/webgl/audio/chrome 补丁
    ├── event_dispatch.js       # 新：trajectory → 真实鼠标事件派发
    └── invariants.js           # 新：cd[] 字段期望值覆盖
```

**删除**：
- `src/crack_tcaptcha/tdc/scrapling_browser.py`
- `pyproject.toml` 的 `scrapling[all]` 依赖
- `client.py` 的 provider 二选一逻辑（固化为 `NodeJsdomProvider`）

### 数据流

```
Pipeline (image_select / slider / icon_click)
    │  Trajectory{kind, points[{x,y,t}], total_ms}
    ▼
NodeJsdomProvider.collect(tdc_url, trajectory, ua)
    │  JSON stdin → node subprocess
    ▼
tdc_executor.js
    1. require profile.js → UA/screen/nav 档案
    2. new JSDOM(url="https://t.captcha.qq.com/template/drag_ele.html")
    3. env_patch.js(win)  → 环境补丁
    4. fetch tdc.js 源码（Referer: https://captcha.gtimg.com/, Origin: https://t.captcha.qq.com）
    5. 注入 <script> 执行，轮询等 window.TDC 就绪
    6. invariants.js.applyInvariants(win)  → cd[] 覆盖
    7. event_dispatch.js.dispatch(win, trajectory)
         - kind="slider"      → TDC.setData(slideData)        (保留现状)
         - kind="click"       → mousemove→down→up→click
         - kind="multi_click" → 漂移 mousemove → 目标点击序列
    8. await sleep(250ms)
    9. TDC.getData(true) + TDC.getInfo() → { collect, eks, tokenid }
    │
    ▼
TDCResult(collect, eks, tlg=len(collect))
```

---

## 4. Trajectory 模型扩展

### `models.py`

```python
TrajectoryKind = Literal["slider", "click", "multi_click"]

@dataclass
class Trajectory:
    kind: TrajectoryKind
    points: list[Point]
    total_ms: int
```

**默认值规则**：所有现有构造点必须显式传 `kind`；类型检查保证迁移一次到位。

### `trajectory.py` 新增函数

```python
def build_slide_trajectory(...) -> Trajectory:
    # 现有逻辑不动，返回时设 kind="slider"
    ...

def build_click_trajectory(target_x, target_y, *,
                           canvas_w=672, canvas_h=480) -> Trajectory:
    """icon_click 单点点击。
    起点随机(漂移区) → 贝塞尔曲线到目标 → 最后一点是目标点。
    """
    ...
    return Trajectory(kind="click", points=pts, total_ms=total)

def build_image_select_trajectory(target_x, target_y) -> Trajectory:
    """image_select（click_image_uncheck）。轨迹结构同 click，kind 不同以便 JS 端路由。"""
    traj = build_click_trajectory(target_x, target_y)
    return replace(traj, kind="multi_click")
```

**轨迹真实性要点**：
- 相邻点时间间隔 jitter：正态 μ=15ms σ=5ms, min=8ms。
- 路径非直线：贝塞尔或 Catmull-Rom + 小幅高频抖动。
- 速度先快后慢（减速接近目标）。
- 漂移段占 30–50%，移动段占 50–70%。
- 参考 `flashtcaptcha/collect/trajectory.py` 中 `generate_click_path` 的实现。

---

## 5. Profile 与环境补丁

### `js/profile.js`

单一默认档案（YAGNI，不做多档案）。从 `flashtcaptcha/collect/profile.py` 移植：

```javascript
module.exports = {
  ua: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      + "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
  platform: "Win32",
  languages: ["zh-CN", "zh", "en"],
  language: "zh-CN",
  hardwareConcurrency: 8,
  deviceMemory: 8,
  maxTouchPoints: 0,
  vendor: "Google Inc.",
  screen: { width: 1920, height: 1080, availWidth: 1920, availHeight: 1040,
            colorDepth: 24, pixelDepth: 24,
            orientation: { type: "landscape-primary", angle: 0 } },
  viewport: { innerWidth: 1280, innerHeight: 720,
              outerWidth: 1920, outerHeight: 1040, devicePixelRatio: 1 },
  webgl: { vendor: "Google Inc. (Intel)",
           renderer: "ANGLE (Intel, Intel(R) UHD Graphics 630, D3D11)" },
  plugins: [ /* 5 个标准 Chrome plugin 条目 */ ],
  timezoneOffset: -480,  // UTC+8
  chromeVersion: 131,
};
```

### `js/env_patch.js`

必须覆盖以下项（tdc.js 的 37 个 cd[] 模块会探测）：

1. **navigator**: userAgent, platform, languages, language, hardwareConcurrency,
   deviceMemory, vendor, plugins, mimeTypes, connection(effectiveType="4g"),
   并**删除** `webdriver` 属性（而非设为 false —— 真实浏览器没有这个属性）。
2. **screen + window**: 按 profile 写死 width/height/availWidth/availHeight/
   innerWidth/innerHeight/outerWidth/outerHeight/devicePixelRatio。
3. **window.chrome**: `{ runtime: {}, loadTimes: ()=>({...}), csi: ()=>({...}) }`。
4. **Canvas fingerprint**: `HTMLCanvasElement.prototype.toDataURL` 补丁返回固定
   base64（从真实 Chrome 上抓取过一份）。
5. **WebGL fingerprint**: `WebGLRenderingContext.prototype.getParameter` 对
   VENDOR/RENDERER/UNMASKED_VENDOR_WEBGL/UNMASKED_RENDERER_WEBGL 返回
   profile.webgl 对应值。
6. **AudioContext / OfflineAudioContext**: stub 固定 buffer。
7. **Fonts**: `document.fonts.check/load` 返回固定字体集合。
8. **performance.timing / timeOrigin**: 填充一组真实的 navigation timing。
9. **不要** 固化 `Date` / `Math.random` —— tdc.js 会混时间戳，固死会被识别为异常。

### `js/invariants.js`

从 `flashtcaptcha/tdc/invariants.py` 抄"哪些 cd[] 索引、哪些子字段必须是什么值"的映射。
在 `TDC.getData()` 调用之前执行 patch：

```javascript
module.exports = function applyInvariants(win) {
  const tdc = win.TDC;
  // e.g. 如果 cd[3] 的 k1 必须是 "v1"
  // 具体映射照抄 flashtcaptcha/tdc/invariants.py
  ...
};
```

**这是 errorCode=9 的 single biggest lever**：服务端静态 XTEA 解 collect 后逐字段
校验；真浏览器仍 9 的原因大概率是某个 cd 模块的 logic 在当前 DOM 下产出被污染。

### Origin / Referer

- `new JSDOM(html, { url: "https://t.captcha.qq.com/template/drag_ele.html" })`
- fetch tdc.js 时手动带：
  - `Referer: https://captcha.gtimg.com/`
  - `Origin: https://t.captcha.qq.com`
  - 保留 `Accept-Encoding: gzip, deflate` 及解压逻辑。

---

## 6. 事件派发

### `js/event_dispatch.js`

按 `trajectory.kind` 分发：

```javascript
async function dispatch(win, trajectory) {
  switch (trajectory.kind) {
    case "slider":      return dispatchSlider(win, trajectory);
    case "click":       return dispatchClick(win, trajectory);
    case "multi_click": return dispatchMultiClick(win, trajectory);
    default: throw new Error("unknown trajectory.kind: " + trajectory.kind);
  }
}
```

### `dispatchSlider`（保留现状，零回归）

```javascript
const slideData = {};
for (const pt of trajectory.points) slideData[pt.t] = `${pt.x},${pt.y}`;
win.TDC.setData(slideData);
// 不派发任何事件 —— 保留当前能跑/不能跑的状态
```

### `dispatchClick`（icon_click）

- 前置：4–6 次阅读期漂移（距目标 ±80px, 80–180ms 间隔）。
- 按 `trajectory.points` 时间戳派发 `mousemove`（每点间隔以时间戳为准，最小 8ms）。
- 到最后一个点：`mousedown` → 40–80ms → `mouseup` → `click`。
- 事件 dispatch 到 `document`，属性齐全（clientX/Y, screenX/Y, pageX/Y, button=0,
  buttons, bubbles=true, cancelable=true）。
- **必须用 `new win.MouseEvent(...)` 构造**，而非全局 `MouseEvent`，否则 tdc.js
  的 instanceof 检查会失败。

### `dispatchMultiClick`（image_select，本次核心场景）

- `trajectory.points` 的最后一点是点击目标，前面是漂移轨迹。
- 漂移段用 `mousemove`。
- 目标点执行完整 `mousemove → mousedown → mouseup → click` 序列。
- **只点一次**（对应 `data_type=DynAnswerType_UC` 的单次 region 选择）。

### 公共处理

- 每个 `dispatchEvent` 用 `setTimeout(fn, 0)` 包装，走 jsdom event loop。
- 派发完毕后 `await sleep(250ms)` flush 事件队列，再 `getData`。

---

## 7. Python subprocess bridge

### `NodeJsdomProvider.collect` 负载

```python
payload = {
    "tdc_url": tdc_url,
    "ua": ua,
    "trajectory": {
        "kind": trajectory.kind,
        "points": [{"x": p.x, "y": p.y, "t": p.t} for p in trajectory.points],
        "total_ms": trajectory.total_ms,
    },
    "debug": settings.tdc_debug,
}
```

### Settings 新增

```python
tdc_debug: bool = False          # True → executor 输出 cd[] dump 到 stderr
tdc_node_path: str = "node"      # 可指定 node 可执行
tdc_profile: str = "chrome131_win"  # 预留多档案，当前只实现一个
```

### 错误分层

| 层 | 错误 | 处理 |
|---|---|---|
| 启动 | `node` 不存在 | `TDCError("node not found: install nodejs and run npm install in js/")` |
| timeout | hang | `TDCError("tdc_executor.js timed out after {N}s")`，附 stderr 前 500 字节 |
| exit != 0 | JS 异常 | 解析 stderr 最后一行，包到 `TDCError` |
| stdout 非 JSON | 诊断污染 | `TDCError("invalid JSON, last line: ...")` |
| collect 空 | `TDC.getData()` 返回 '' | `TDCError("empty collect")` |
| tokenid 缺 | `getInfo()` 异常 | 仅 log.warning |

---

## 8. 实施阶段

| # | 范围 | 独立验证 |
|---|---|---|
| P0 | `models.Trajectory.kind` + `trajectory.py` + 三个 pipeline 调用侧，kind 默认 `"slider"` 行为不变 | 单测 + slider 回归 |
| P1 | Node 侧骨架：拆 `tdc_executor.js` → 5 个文件；`dispatchSlider` 先搬旧逻辑 | slider 端到端回归通过 |
| P2 | `profile.js` + `env_patch.js`；origin 改 `t.captcha.qq.com` | window 属性单测 + slider 回归 |
| P3 | `dispatchClick` / `dispatchMultiClick` + `build_click_trajectory` / `build_image_select_trajectory` | 事件派发单测 + image_select 手测：errorCode 从"collect 格式错"推进到"cd[] 校验错" |
| P4 | `invariants.js`（从 flash 抄 cd[] 覆盖表）；`applyInvariants` 在 getData 前执行 | debug 开关下 diff 干净 + image_select 出 ticket |
| P5 | 删 `scrapling_browser.py`；pyproject 去 `scrapling[all]`；`client.py` 固化 `NodeJsdomProvider` | 全量 test pass；依赖树缩小 |
| P6 | 三场景手测 + 补测试 | 三类各 1/5 以上通过 |

每阶段一个 commit（**由用户手动 commit，Claude 不自动 commit**），每阶段可独立 revert。

---

## 9. 测试策略

### 新增测试文件

| 文件 | 覆盖 |
|---|---|
| `tests/tdc/test_jsdom_provider.py` | provider 启动、序列化、解析；mock tdc.js 返回固定 collect |
| `tests/tdc/test_event_dispatch.py` | click trajectory → mock tdc.js 捕获 mousemove+down+up+click 序列 |
| `tests/tdc/test_slider_regression.py` | kind='slider' 仍走 setData、不派发 mouse events |
| `tests/tdc/test_profile.py` | env_patch 后 navigator.userAgent/screen.width 等符合 profile |
| `tests/pipelines/test_image_select_jsdom.py` | e2e mock：verify POST body 里 collect 非空、kind 路径对 |

### 手测剧本（验收必跑）

1. `uv run crack-tcaptcha solve --appid 2046626712` → image_select，期望 5 次内 ≥1 成功。
2. 现有 slider appid → 通过率不低于重构前。
3. 现有 icon_click appid → 通过率不低于重构前。

### Debug 开关

```bash
CRACK_TCAPTCHA_TDC_DEBUG=1 uv run crack-tcaptcha solve --appid 2046626712
```

- executor stderr 输出 cd[] hexdump。
- Python 侧将 cd[] 逐项 diff 到 `flashtcaptcha/tdc/invariants.py` 期望值。

### CI

- Node subprocess 相关测试在无 `node` 环境下跳过：
  `@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")`

---

## 10. 风险与缓解

| 风险 | 缓解 |
|---|---|
| flash 的 harvest 来自旧版 tdc.js，invariants 已漂移 | P4 做 debug diff，人工对齐最新 tdc.js 的 cd[] 实际输出 |
| jsdom 的 MouseEvent 在 tdc.js instanceof 检查下失败 | 必用 `new win.MouseEvent(...)`；测试显式断言 instanceof |
| invariants 覆盖太狠触发反向检测（"太完美 → 机器人"） | Math.random / Date 不固化；canvas/webgl 用真实抓取值而非全零/全一 |
| Node 版本差异导致 jsdom 行为不一致 | `js/package.json` lockfile；README 标 Node ≥ 20 |

---

## 11. 验收清单（Definition of Done）

- [ ] `uv run crack-tcaptcha solve --appid 2046626712` 5 次内 ≥1 非空 ticket。
- [ ] slider 回归：现有测试 appid 通过率 ≥ 重构前。
- [ ] icon_click 回归：同上。
- [ ] `scrapling` 不出现在运行时 import 链。
- [ ] `pytest` 全绿（node 缺失导致的跳过不算失败）。
- [ ] `CRACK_TCAPTCHA_TDC_DEBUG=1` 能人肉可读地输出 cd[] diff。
- [ ] `pyproject.toml` 移除 `scrapling[all]` 依赖，`uv.lock` 重生成。

---

## 12. 参考 / 来源

- `flashtcaptcha/collect/profile.py` — UA/screen/webgl/plugins 档案。
- `flashtcaptcha/collect/trajectory.py` — click 路径生成函数。
- `flashtcaptcha/collect/builder.py` — collect 字段组装参考（仅参考字段顺序/取值）。
- `flashtcaptcha/tdc/invariants.py` — cd[] 字段期望值映射（核心）。
- `flashtcaptcha/tdc/module_fp.py` — tdc.js 模块指纹（参考，用于版本判定）。
- `artifacts/harvest/` — 各 show_type 的 prehandle 响应样本。
- 最近 commit `6b9edbd` / `86ea2e4` / `77f120a` — endpoint / referer 修复历史。
