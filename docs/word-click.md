# 文字点选（word_click）

TCaptcha 2.0 的文字点选挑战。提示词（目标汉字序列）内嵌在 `instruction` 中，形如 `请依次点击：张 明 伟 `，前景精灵图 `fg_elem_list` 为空。

## 流程

1. `prehandle` 返回 `instruction`（含目标汉字）、背景图 URL、`pow_cfg`；`fg_elem_list` 为空
2. 下载背景图（`bg_elem_cfg.size_2d = [672, 480]`）
3. **主路径：YOLOv8 检测 + Siamese 匹配（本地 ONNX，纯 CPU 约 200 ms）**
   1. `word_click_detector.onnx`（YOLOv8）在背景图上定位所有字符 bbox
   2. 用打包在仓库里的 `font.ttf` 把 `instruction` 里每个目标汉字渲染成 52×52 参考图
   3. 对每一对 `(bbox 裁剪图, 目标参考图)` 跑 `word_click_matcher.onnx`（Siamese）得相似度
   4. 对每个目标按指令顺序贪心取当前未使用的最高分 bbox
4. **回退路径：ddddocr**（仅在 YOLO 检测结果为 0、或未安装 `[word-click]` extra 时走） —— `detection + classification` 子串匹配
5. 按提示顺序把每个汉字映射到 bbox 中心坐标
6. 生成点击轨迹，通过 TDC 桥取 `collect` / `eks`
7. POST `verify`

详见 `src/crack_tcaptcha/pipelines/word_click.py` 与 `src/crack_tcaptcha/solvers/word_ocr.py`。

## 模型与依赖

装 `word-click` extra：

```bash
uv sync --extra word-click
# 或
pip install 'crack-tcaptcha[word-click]'
```

这会安装：

| 包 | 作用 |
|---|---|
| `onnxruntime` | 跑 YOLO + Siamese ONNX |
| `opencv-python-headless` | 图像预处理（letterbox、裁剪、colorspace） |
| `ddddocr` | 回退路径（YOLO 检测失败时兜底） |

模型文件已通过 hatch `force-include` 打包进 wheel，开箱即用：

| 文件 | 大小 | 作用 |
|---|---|---|
| `solvers/models/word_click_detector.onnx` | 10 MB | YOLOv8 检测器，输出字符 bbox |
| `solvers/models/word_click_matcher.onnx` | 29 MB | Siamese 相似度网络（输入 52×52×3×2，输出 1 个相似度） |
| `solvers/models/font.ttf` | 4.6 MB | 目标字渲染字体（颜色 BGR `(56,178,227)` 模拟验证码配色） |

## 性能调优

ONNX Runtime 执行后端通过环境变量选择，默认 `auto` 优先挑 `CUDA > ROCm > DML > CoreML > CPU`：

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `TCAPTCHA_ORT_BACKEND` | `auto` | 强制后端：`cpu` / `cuda` / `rocm` / `dml` / `coreml` / `auto` |
| `TCAPTCHA_ORT_INTRA_OP_THREADS` | `min(4, cpu_count)` | ORT 线程数；Siamese 太小，>4 反而更慢 |

**macOS 提示**：默认会选 CoreML EP，首次推理需要一次性的 graph-compile 开销（几秒），总耗时可能比纯 CPU 还高。重复调用请用常驻服务模式，或 `export TCAPTCHA_ORT_BACKEND=cpu` 固定 CPU。

### 常驻服务模式

一次性 CLI 每次都付 Python 启动 + ONNX 加载的冷启动成本。生产 / 重复调用请用 `serve` 子命令：

```bash
# 启动（模型在进程启动时只加载一次）
TCAPTCHA_SERVE_SK=change-me crack-tcaptcha serve --port 9991 --workers 4

# 客户端
curl -H 'X-SK: change-me' -X POST http://127.0.0.1:9991/solve \
    -d '{"appid":"YOUR_APPID","retries":3}'
```

## LLM 不再需要

之前版本主路径走 OpenAI 兼容 `/v1/chat/completions`；本地模型 + ddddocr 兜底足够覆盖所有已知 word_click 样本，LLM 相关环境变量（`TCAPTCHA_LLM_*`）只对 `image_select` 还有意义，可留空。

## Answer 格式

```json
[
  {"elem_id": 1, "type": "DynAnswerType_POS", "data": "123,45"},
  {"elem_id": 2, "type": "DynAnswerType_POS", "data": "256,98"},
  {"elem_id": 3, "type": "DynAnswerType_POS", "data": "401,112"}
]
```

`elem_id` 从 1 开始按 `instruction` 中的目标顺序递增；`data` 是 bbox 中心点的 `x,y`（背景图像素坐标）。

## 常见坑

- **提示词里混入标点或空格** —— `_parse_target_chars` 用 `[\u4e00-\u9fff]` 正则只抓 CJK 汉字
- **bbox 数量少于目标字数** —— YOLO 漏检，pipeline 会记 warning 后仍尽力回答；验证失败时外层 `max_retries` 会重拉一次 prehandle
- **YOLO 返回 0 bbox** —— 自动走 ddddocr detect + classification 兜底
- **CoreML EP 首次慢** —— 见上节；`serve` 模式或 `TCAPTCHA_ORT_BACKEND=cpu` 解决
- **模型文件被安全软件误删** —— 重装 `[word-click]` extra 即可；模型在 wheel 里
- **最后兜底** —— 如果所有方法都没给某个字找到 bbox（极少见），会把它分配到第一个未被使用的 bbox，至少保证一次可见的点击（而不是 `(0,0)` 的显式错误）
