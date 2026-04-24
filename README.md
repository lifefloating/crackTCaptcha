# crack-tcaptcha

> 腾讯 T-Sec 天御验证码（TCaptcha）自动化求解器 —— 支持滑块、图标点击、文字点选、图像选择。
> 纯 HTTP 协议实现，**无需 Selenium / Playwright 等浏览器自动化**，依赖 Node.js + jsdom 桥接 TDC.js。

![verify ok](images/word-click-success.png)

> 上图为 word_click（文字点选）流水线的真实运行日志：从 `prehandle` → `getcapbysig` 下载背景图 → **本地 YOLO + Siamese 模型**给出点击坐标 → `nodejs_jsdom` 采集 TDC collect / eks / pow → `cap_union_new_verify` 一次通过，`ok=true`。

## 特性

- **4 种验证类型**：`slider`（滑块）、`icon_click`（图标点击）、`word_click`（文字点选）、`image_select`（图像选择）
- **无头浏览器依赖**：`nodejs_jsdom` 在 Node.js 进程里用 jsdom 跑官方 TDC.js，生成 `collect / eks / tokenid / pow_answer`
- **策略化求解器**：滑块使用 OpenCV 模板匹配；`word_click` 走本地 **YOLOv8 检测 + Siamese 匹配**（纯 ONNX Runtime，单次 ~200 ms）；`icon_click` 使用 `ddddocr`；`image_select` 使用 OpenAI 兼容 LLM vision
- **常驻 HTTP 服务**：`crack-tcaptcha serve` 让模型只加载一次，每次求解只付推理时间（零进程冷启动）
- **工程化**：pydantic-settings 配置、结构化日志、CLI、pytest，类型完整

## 当前测试状态

| 类型 | 状态 | 备注 |
|---|---|---|
| `word_click`（文字点选） | ✅ 已跑通（见上图） | 本地 YOLO + Siamese 模型，一次通过 |
| `slider`（滑块） | 🧪 未充分验证 | pipeline 已实现，仅做过少量手工测试 |
| `icon_click`（图标点击） | 🧪 未充分验证 | pipeline 已实现，依赖 `ddddocr`，待回归 |
| `image_select`（图像选择） | 🧪 未充分验证 | pipeline 已实现，待回归 |

> 目前项目重点打磨 `word_click`，其它类型欢迎 PR 补测试样本 / 回归用例。

## 安装

### 按需求选择

```bash
# 最小安装：仅 slider pipeline（HTTP + 轨迹生成，无 ML 依赖）
uv add crack-tcaptcha

# 文字点选（本地 YOLO + Siamese 模型；含 ddddocr 作为 OCR 兜底）
uv add "crack-tcaptcha[word-click]"

# 图标点击（仅 ddddocr）
uv add "crack-tcaptcha[icon-click]"

# 中文图像选择（cn-clip / torch，下载模型约数百 MB）
uv add "crack-tcaptcha[clip]"

# 全功能一键装（= word-click + icon-click + clip）
uv add "crack-tcaptcha[all]"
```

也可以用 `pip` 替代 `uv add`，语法一致：`pip install 'crack-tcaptcha[word-click]'`。

| Extra | 引入依赖 | 启用的 pipeline |
|---|---|---|
| _(none)_ | httpx / pydantic / numpy / Pillow / scrapling | `slider` |
| `icon-click` | `ddddocr`（+ onnxruntime） | `icon_click` |
| `word-click` | `onnxruntime` + `opencv-python-headless` + `ddddocr` | `word_click`（本地 YOLO + Siamese，OCR 兜底） |
| `clip` | `cn2an`、`cn-clip`、`torch` | `image_select`（CLIP backend） |
| `all` | 以上全部 | 所有 pipeline |

> 未装 `[word-click]` / `[icon-click]` 时，对应 pipeline 抛出清晰的 `SolveError` 提示应装哪个 extra。

> `word_click` 的本地模型（`word_click_detector.onnx` 10 MB、`word_click_matcher.onnx` 29 MB、`font.ttf` 4.6 MB）已随 wheel 打包，安装后开箱即用，无需额外下载。

### 前置要求

- Python >= 3.10
- Node.js >= 18（用于 TDC.js 桥）

```bash
cd src/crack_tcaptcha/tdc/js && npm install
```

## 快速上手

### Python API

```python
from crack_tcaptcha import solve, TCaptchaType

result = solve(
    appid="YOUR_APPID",            # 换成你自己的 TCaptcha APP_ID
    challenge_type=TCaptchaType.SLIDER,
    max_retries=3,
    entry_url="https://your-site.example.com/login",  # 可选，携带 Referer/Origin
)
if result.ok:
    print(result.ticket, result.randstr)
```

### 命令行

```bash
# 一次性求解：--appid 替换为你自己的 APP_ID
crack-tcaptcha solve --appid YOUR_APPID --retries 3 --json

# 指定来源页（会带上对应 Referer / Origin）
crack-tcaptcha solve --appid YOUR_APPID --entry-url https://example.com/login --json
```

### 常驻 HTTP 服务（推荐用于重复调用）

一次性 CLI 每次都要冷启动 Python + 加载 ONNX 模型，首次可能要花几秒。常驻模式模型只加载一次，后续请求只付推理时间：

```bash
# 启动（鉴权可选：导出 TCAPTCHA_SERVE_SK 后客户端需带 X-SK header）
export TCAPTCHA_SERVE_SK=change-me
crack-tcaptcha serve --port 9991 --workers 4

# 客户端：POST /solve
curl -H 'X-SK: change-me' -X POST http://127.0.0.1:9991/solve \
    -d '{"appid":"YOUR_APPID","retries":3}'

# 健康检查
curl http://127.0.0.1:9991/health
```

> 命令行示例中的 `YOUR_APPID` 仅为占位符，请替换为你自己的 appid；仓库不提供任何真实业务 appid。

## 本地测试页

仓库附带一个最小化的 TCaptcha 2.0 加载页 `examples/tcap2_loader.html`，可用于本地联调：

```bash
# 1. 编辑 examples/tcap2_loader.html，把 YOUR_APPID 替换为你自己的 appid
# 2. 启动静态服务器
cd examples && python3 -m http.server 8765
# 3. 浏览器打开 http://localhost:8765/tcap2_loader.html
# 4. 另起终端用 CLI 调本地页面的 Referer 进行求解
crack-tcaptcha solve --appid YOUR_APPID --entry-url http://localhost:8765/tcap2_loader.html --json
```

页面会把官方回调的 `ret / ticket / randstr / errorMessage` 渲染出来，便于对比服务端返回。

## 架构概览

```
src/crack_tcaptcha/
├── client.py              # HTTP 三段式：prehandle / getcapbysig / verify
├── cli.py                 # argparse 入口（solve / serve 子命令）
├── server.py              # 常驻 HTTP 服务
├── pow.py                 # PoW 求解
├── trajectory.py          # 轨迹 / 点击序列合成
├── captcha_type.py        # 类型分发路由
├── pipelines/             # 每种验证类型一个 pipeline
│   ├── slide.py
│   ├── icon_click.py
│   ├── word_click.py      # 本地 YOLO 检测 + Siamese 匹配（含 ddddocr 兜底）
│   └── image_select.py
├── solvers/
│   ├── ort_provider.py    # ORT execution-provider 选择（CUDA/ROCm/DML/CoreML/CPU）
│   ├── word_ocr.py        # YOLO + Siamese 求解器（word_click 主路径）
│   ├── llm_vision.py      # OpenAI 兼容 LLM vision（image_select 用）
│   └── models/            # 打包的 ONNX 模型 + font.ttf
└── tdc/                   # TDC.js 桥
    ├── js/                # npm install 后的 node_modules
    └── nodejs_jsdom.py    # jsdom NodeProvider
```

详见 `docs/architecture.md`。

## 配置

通过环境变量 / `.env` / 构造参数（前缀 `TCAPTCHA_`）：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `TCAPTCHA_USER_AGENT` | Chrome 147 UA | 浏览器 UA |
| `TCAPTCHA_BASE_URL` | `https://turing.captcha.qcloud.com` | 接口根 |
| `TCAPTCHA_TIMEOUT` | `15.0` | 单请求超时 |
| `TCAPTCHA_MAX_RETRIES` | `3` | 最大重试 |
| `TCAPTCHA_TDC_TIMEOUT` | `60.0` | TDC.js 桥超时 |
| `TCAPTCHA_TDC_DEBUG` | `false` | 打开后保留 jsdom 调试日志 |
| `TCAPTCHA_PROXY` | `None` | `http://user:pass@host:port` |
| `TCAPTCHA_LLM_API_KEY` | `""` | LLM vision 求解器（仅 `image_select` 需要） |
| `TCAPTCHA_LLM_BASE_URL` | `""` | OpenAI 兼容接口根 |
| `TCAPTCHA_LLM_MODEL` | `gpt-5.4` | 模型名 |
| `TCAPTCHA_ORT_BACKEND` | `auto` | ONNX 执行后端：`auto` / `cpu` / `cuda` / `rocm` / `dml` / `coreml` |
| `TCAPTCHA_ORT_INTRA_OP_THREADS` | `min(4, cpu_count)` | ORT 线程数（Siamese 在 >4 时反而更慢） |
| `TCAPTCHA_SERVE_SK` | `""` | 常驻服务鉴权 secret；非空时请求必须带 `X-SK` header |
| `TCAPTCHA_SERVE_HOST` | `127.0.0.1` | `serve` 子命令监听地址 |
| `TCAPTCHA_SERVE_PORT` | `9991` | `serve` 子命令监听端口 |
| `TCAPTCHA_SERVE_WORKERS` | `4` | `serve` 并发 solve 上限 |

> macOS 下若首次求解明显慢，通常是 CoreML 后端的图编译开销；
> 导出 `TCAPTCHA_ORT_BACKEND=cpu` 往往比默认更快。

## 开发

```bash
uv sync --all-extras
uv run ruff check .
uv run pytest -x -ra
uv run pytest -m "not network"   # 跳过联网用例
```

## 免责声明

本项目 **仅用于个人安全研究、技术学习与学术交流**，不代表任何商业机构的立场。

- 本仓库所有代码、文档、示例均为作者个人基于公开协议的逆向分析与学习笔记，**不包含任何非公开资料、密钥或私有接口**。
- **严禁** 将本项目用于：批量注册、抢购黄牛、撞库、刷量、非法爬取、绕过付费墙、对抗风控等任何违反目标站点服务条款、《网络安全法》《数据安全法》《个人信息保护法》及其他适用法律法规的行为。
- 使用者须自行承担因使用本项目而产生的一切后果与法律责任。作者及贡献者 **不对任何直接、间接、附带、衍生的损失负责**。
- 如果你是相关权利方并认为本项目存在不当内容，请通过 issue 联系作者，我们会在合理时间内处理。

**下载、克隆或使用本项目，即视为你已阅读、理解并同意以上全部条款。**

## License

GPL-3.0-or-later
