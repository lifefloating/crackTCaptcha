# crack-tcaptcha

> 腾讯 T-Sec 天御验证码（TCaptcha）自动化求解器 —— 支持滑块、图标点击、文字点选、图像选择。
> 纯 HTTP 协议实现，**无需 Selenium / Playwright 等浏览器自动化**，依赖 Node.js + jsdom 桥接 TDC.js。

![verify ok](images/word-click-success.png)

> 上图为 word_click（文字点选）流水线的真实运行日志：从 `prehandle` → `getcapbysig` 下载背景图 → LLM 视觉给出点击坐标 → `nodejs_jsdom` 采集 TDC collect / eks / pow → `cap_union_new_verify` 一次通过，`ok=true`。

## 特性

- **4 种验证类型**：`slider`（滑块）、`icon_click`（图标点击）、`word_click`（文字点选）、`image_select`（图像选择）
- **无头浏览器依赖**：`nodejs_jsdom` 在 Node.js 进程里用 jsdom 跑官方 TDC.js，生成 `collect / eks / tokenid / pow_answer`
- **策略化求解器**：滑块使用 OpenCV 模板匹配；点击类支持 `ddddocr` / 任意 OpenAI 兼容的 LLM vision
- **工程化**：pydantic-settings 配置、结构化日志、CLI、pytest，类型完整

## 当前测试状态

| 类型 | 状态 | 备注 |
|---|---|---|
| `word_click`（文字点选） | ✅ 已跑通（见上图） | LLM vision 映射字→bbox，一次通过 |
| `slider`（滑块） | 🧪 未充分验证 | pipeline 已实现，仅做过少量手工测试 |
| `icon_click`（图标点击） | 🧪 未充分验证 | pipeline 已实现，依赖 `ddddocr`，待回归 |
| `image_select`（图像选择） | 🧪 未充分验证 | pipeline 已实现，待回归 |

> 目前项目重点打磨 `word_click`，其它类型欢迎 PR 补测试样本 / 回归用例。

## 安装

### 按需求选择

```bash
# 最小安装：仅 slider pipeline（HTTP + 轨迹生成，无 ML 依赖）
uv add crack-tcaptcha

# 推荐：图标点击 + 文字点选（word_click 也依赖 ddddocr）
uv add "crack-tcaptcha[icon-click]"

# 中文图像选择（cn-clip / torch，下载模型约数百 MB）
uv add "crack-tcaptcha[clip]"

# 全功能一键装（= icon-click + clip）
uv add "crack-tcaptcha[all]"
```

也可以用 `pip` 替代 `uv add`，语法一致：`pip install 'crack-tcaptcha[icon-click]'`。

| Extra | 引入依赖 | 启用的 pipeline |
|---|---|---|
| _(none)_ | 仅 httpx / pydantic / numpy / Pillow | `slider` |
| `icon-click` | `ddddocr`（+ onnxruntime） | `icon_click`、`word_click` |
| `clip` | `cn2an`、`cn-clip`、`torch` | `image_select`（CLIP backend） |
| `all` | 以上全部 | 所有 pipeline |

> 运行 `word_click` / `icon_click` 前未装 `[icon-click]` 会得到清晰的 ModuleNotFoundError 提示。

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
# 通用求解：--appid 替换为你自己的 APP_ID
crack-tcaptcha solve --appid YOUR_APPID --retries 3 --json

# 指定来源页（会带上对应 Referer / Origin）
crack-tcaptcha solve --appid YOUR_APPID --entry-url https://example.com/login --json
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
├── client.py              # HTTPX 客户端：prehandle / getcapbysig / verify
├── pow.py                 # PoW 求解
├── trajectory.py          # 轨迹/点击序列合成
├── captcha_type.py        # 类型分发路由
├── pipelines/             # 每种验证类型一个 pipeline
│   ├── slide.py
│   ├── icon_click.py
│   ├── word_click.py      # 文字点选（对应截图演示）
│   └── image_select.py
├── solvers/llm_vision.py  # OpenAI 兼容 LLM 视觉求解器
└── tdc/                   # TDC.js 桥
    ├── js/                # npm install 后放 node_modules
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
| `TCAPTCHA_LLM_API_KEY` | `""` | LLM vision 求解器（`image_select` / `word_click`） |
| `TCAPTCHA_LLM_BASE_URL` | `""` | OpenAI 兼容接口根 |
| `TCAPTCHA_LLM_MODEL` | `gpt-5.4` | 模型名 |

## 开发

```bash
uv sync --all-extras
uv run ruff check .
uv run pytest -x -ra
uv run pytest -m "not network"   # 跳过联网用例
```

## 推荐 — 用本地模型替换 LLM vision

当前 `word_click` / `image_select` 走 OpenAI 兼容接口，单次推理 **1~3 s** 起步（受网络、排队、token 数影响），是整条链路里最慢的一步。
本地模型可以把这一步压到 **≤200 ms**，且无调用成本 / 限流 / 数据出站风险。

两类任务本质都是 **"把一张图映射到一个确定的类别 / 索引"**，不需要真正的生成式 VLM：

### 方案 A：PaddleOCR + 轻量匹配（推荐）

| 子任务 | 本地替代 |
|---|---|
| `word_click`：识别背景图 3 个 bbox 里各是什么汉字 | **PaddleOCR** (`ch_PP-OCRv4`)，单字裁剪后 OCR → 与指令中的字做字符串匹配 |
| `image_select`：在 N 宫格里挑"哪个是苹果" | **PaddleClas PP-LCNet / PP-ShiTu** 或 **cn-clip ViT-B/16**（已列在 `[clip]` extras） |

优点：CPU 可跑、模型 <20 MB、推理 10~50 ms；PaddleOCR 对中文场景文字鲁棒性很好。

### 方案 B：CLIP 类零样本匹配

直接复用仓库里已经声明过的 `cn-clip` 依赖：

```bash
uv add "crack-tcaptcha[clip]"
```

- `word_click`：把每个 bbox 裁剪图与 "一张写着'X'字的图" 做 image-text 相似度 argmax（但中文单字 CLIP 准确率一般，建议配合 OCR 投票）
- `image_select`：把指令"请选出所有包含苹果的图片"直接作为 text query，对 N 个格子打分排序，取 top-k

优点：一个模型吃下所有"图→文"匹配场景；缺点：模型 ~400 MB，冷启动有成本。

### 方案 C：ddddocr + 本地分类头（最轻）

- `icon_click` 已经在用 `ddddocr`；`word_click` 的 bbox 识别也可以换 `ddddocr.DdddOcr(det=False)`（纯 OCR 模式）
- 对 `image_select` 训一个 **PP-LCNet** 分类头（常见类别就那几类：动物、交通工具、食物...）+ "其它"兜底走 CLIP

### 落地建议

1. 在 `solvers/` 下新增 `paddle_ocr.py` 和 `cn_clip.py`，实现与 `llm_vision.py` 同签名（`match_region` / `locate_chars`）
2. 在 `settings.py` 加 `solver_backend: Literal["llm", "paddle", "clip", "ddddocr"] = "llm"`
3. pipeline 启动时根据 backend 路由，保留 LLM 作为兜底（本地模型置信度 < 阈值时回退）
4. 评估指标：单验证码平均耗时、端到端通过率、CPU / 显存占用，基准样本集可用 `tests/samples/`

## 免责声明

本项目 **仅用于个人安全研究、技术学习与学术交流**，不代表任何商业机构的立场。

- 本仓库所有代码、文档、示例均为作者个人基于公开协议的逆向分析与学习笔记，**不包含任何非公开资料、密钥或私有接口**。
- **严禁** 将本项目用于：批量注册、抢购黄牛、撞库、刷量、非法爬取、绕过付费墙、对抗风控等任何违反目标站点服务条款、《网络安全法》《数据安全法》《个人信息保护法》及其他适用法律法规的行为。
- 使用者须自行承担因使用本项目而产生的一切后果与法律责任。作者及贡献者 **不对任何直接、间接、附带、衍生的损失负责**。
- 如果你是相关权利方并认为本项目存在不当内容，请通过 issue 联系作者，我们会在合理时间内处理。

**下载、克隆或使用本项目，即视为你已阅读、理解并同意以上全部条款。**

## License

GPL-3.0-or-later
