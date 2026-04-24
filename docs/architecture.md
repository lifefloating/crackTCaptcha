# 架构总览

## 分层架构

```
┌───────────────────────────────────────────────────────────────┐
│  solve(appid, ...)                  ← crack_tcaptcha/__init__ │
│  crack-tcaptcha serve               ← crack_tcaptcha/server.py │
└──────────────┬────────────────────────────────────────────────┘
               │  classify(dyn)  →  captcha_type
               ▼
┌───────────────────────────────────────────────────────────────┐
│  pipelines/                                                    │
│    ├─ slide.py         ── OpenCV NCC                           │
│    ├─ icon_click.py    ── ddddocr                              │
│    ├─ word_click.py    ── word_ocr (YOLO + Siamese)            │
│    │                       (回退: ddddocr)                     │
│    ├─ image_select.py  ── llm_vision                           │
│    └─ _common.py       ── finish_with_verify / run_async       │
└──────────────┬────────────────────────────────────────────────┘
               │ uses                                   uses
               ▼                                        ▼
   ┌──────────────────────────────┐     ┌──────────────────────┐
   │ solvers/                      │     │ tdc/                 │
   │   ├─ ort_provider.py          │     │   ├─ provider.py     │
   │   ├─ word_ocr.py              │     │   └─ nodejs_jsdom.py │
   │   │    (YOLO + Siamese ONNX)  │     │                      │
   │   ├─ llm_vision.py            │     │                      │
   │   │    (OpenAI 兼容)           │     │                      │
   │   └─ models/                  │     │                      │
   │       word_click_detector.onnx│     │                      │
   │       word_click_matcher.onnx │     │                      │
   │       font.ttf                │     │                      │
   └──────────────────────────────┘     └──────────────────────┘
               │                                        │
               └──────────┬─────────────────────────────┘
                          ▼
      ┌──────────────────────────────────────────┐
      │ client.py (HTTP 三段式 + JSONP 解壳)       │
      │ pow.py   (MD5 PoW)                        │
      │ trajectory.py (滑动/点击轨迹生成)          │
      │ models.py (pydantic 响应模型)              │
      │ settings.py (pydantic-settings)           │
      └──────────────────────────────────────────┘
```

依赖方向严格自上而下：`pipelines` 依赖 `solvers`、`tdc`、`client`；`solvers` 和 `tdc` 互不依赖。`server.py` 只依赖 `__init__.solve` 与 `solvers/word_ocr.warmup`，不直接导入 `pipelines/`。

## 三段式协议

所有端点都在 `https://turing.captcha.qcloud.com`（可通过 `TCAPTCHA_BASE_URL` 覆盖）。

```
┌──────────┐   prehandle    ┌──────────┐   getcapbysig   ┌──────────┐
│  Client   │──────────────▶│  Server   │◀───────────────│  Client   │
│           │◀──────────────│           │───────────────▶│           │
│           │ sess, pow_cfg │           │  bg / fg PNG   │           │
│           │ fg_elem_list  │           │                │           │
│           │               │           │                │           │
│           │    verify     │           │                │           │
│           │──────────────▶│           │                │           │
│           │◀──────────────│           │                │           │
│           │ ticket/randstr│           │                │           │
└──────────┘               └──────────┘                └──────────┘
```

### 阶段 1：`cap_union_prehandle`

- 初始化会话，返回 `sess`、`pow_cfg`、`dyn_show_info`、`bg_elem_cfg`、可选的 `fg_elem_list`
- 每次重试 `subsid` 会自增
- 返回体是 JSONP，`client.py` 中 `parse_jsonp` 剥壳

### 阶段 2：`cap_union_new_getcapbysig`

- `img_index=1` → 背景图
- `img_index=0` → 前景精灵图（slider 的拼图块；icon_click 的提示图标）
- 滑块：背景 672×390 RGB PNG，前景 682×620 RGBA PNG
- 文字点选 / 图像选择：只需要背景图（提示词内嵌在 `instruction`）

### 阶段 3：`cap_union_new_verify`

POST 字段：`ans`、`pow_answer`、`pow_calc_time`、`collect`、`tlg`、`eks`。成功返回 `ticket` + `randstr`。

## Pipeline ↔ Solver 映射

| Pipeline | 核心 Solver | 可选/回退 | 依赖包 |
|---|---|---|---|
| `slide` | `solvers`（内嵌 NCC，见 `pipelines/slide.py` 的 `SliderSolver`） | —— | `numpy`、`Pillow` |
| `icon_click` | `ddddocr` 检测 + 模板匹配 | —— | `ddddocr`（extra `icon-click`） |
| `word_click` | `solvers/word_ocr.locate_chars_by_siamese`（YOLO + Siamese ONNX，本地） | `ddddocr` 检测 + 分类兜底 | `onnxruntime` + `opencv-python-headless` + `ddddocr`（extra `word-click`） |
| `image_select` | `solvers/llm_vision.match_region` | —— | OpenAI 兼容 API |

自动路由：`captcha_type.classify(dyn_show_info)` 是一个纯函数分类器，按规则顺序返回 `slide` / `icon_click` / `word_click` / `image_select` / `unknown`。规则命中后 `pipelines.dispatch` 将请求分发到对应的 pipeline。

## TDC.js 桥接

```
Python ──subprocess──▶ Node.js + jsdom ──eval──▶ tdc.js
                              │
                              ▼
                {collect, eks, tokenid, pow_answer}
```

`tdc/provider.py` 定义了 `TDCProvider` Protocol：任何实现 `async collect(tdc_url, trajectory, ua) -> TDCResult` 的对象都可以被 pipeline 使用。当前仅实现 `NodeJsdomProvider`（`tdc/nodejs_jsdom.py`），未来可以扩展到 Puppeteer 或其他浏览器桥接方案而不需要改动 pipeline 代码。

关键 jsdom 配置在 `tdc/js/tdc_executor.js`：`pretendToBeVisual: true`、`runScripts: "dangerously"`，并 patch `screen`、`innerWidth/Height`、`devicePixelRatio`、`navigator.webdriver`。详细检测向量见 [反向笔记](reverse-notes.md)。

## HTTP 层设计

`client.py` 使用 `scrapling` 的 `Fetcher`（底层 `curl_cffi`）做 Chrome TLS 指纹模拟，绕过腾讯基于 TLS 指纹的 bot 检测。普通的 `httpx` / `requests` / `urllib` 在该端点会直接收到 403。

`entry_url` 参数可选，用于为请求附加合理的 `Referer` / `Origin`。在真实业务站点嵌入时建议传入，空值也能跑通但风控更严格。
