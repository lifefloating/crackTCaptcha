# 文字点选（word_click）

TCaptcha 2.0 的文字点选挑战。提示词（目标汉字序列）内嵌在 `instruction` 中，形如 `请依次点击：张 明 伟 `，前景精灵图 `fg_elem_list` 为空。

## 流程

1. `prehandle` 返回 `instruction`（含目标汉字）、背景图 URL、`pow_cfg`；`fg_elem_list` 为空
2. 下载背景图（`bg_elem_cfg.size_2d = [672, 480]`）
3. `ddddocr` 在背景上检测候选汉字 bbox
4. **主路径：LLM vision** —— 给每个候选 bbox 画红框 + 编号（1..N），和目标汉字一起发给 LLM，要求返回 `{char: box_index}` JSON
5. **回退路径：ddddocr OCR 分类** —— 对 LLM 没映射上的汉字，逐 bbox 跑 `ddddocr.classification` 做子串匹配
6. 按提示顺序把每个汉字映射到 bbox 中心坐标
7. 生成点击轨迹，通过 TDC 桥取 `collect` / `eks`
8. POST `verify`

详见 `src/crack_tcaptcha/pipelines/word_click.py`。

## LLM 配置

求解器实现在 `src/crack_tcaptcha/solvers/llm_vision.py`（`locate_chars` 函数），走 OpenAI 兼容的 `/v1/chat/completions` 接口。

需要的环境变量（pydantic-settings 自动读 `.env` 或环境变量）：

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `TCAPTCHA_LLM_API_KEY` | Bearer token | `""`（未配置时 LLM 路径会跳过） |
| `TCAPTCHA_LLM_BASE_URL` | 不含 `/v1/...` 的基础 URL | `""` |
| `TCAPTCHA_LLM_MODEL` | 模型名 | `gpt-5.4` |
| `TCAPTCHA_LLM_TIMEOUT` | HTTP 超时秒数 | `30` |

**支持的后端**：任何 OpenAI 兼容的中继服务（官方 OpenAI、Azure OpenAI 代理、自建 vLLM / llama.cpp 的 OpenAI shim 等）。请求体用 `image_url` 传 base64 图片，返回必须是能解析出 JSON 对象的文本。

**Prompt 位置**：`_build_word_click_prompt()` 和 `_build_prompt()` 函数在 `solvers/llm_vision.py` 顶部，prompt 硬编码在 Python 中，方便直接修改；调整后不需要改 pipeline。

如果没有配置 LLM（key / base_url 任一为空），pipeline 会走纯 ddddocr OCR 回退；经验上 ddddocr 对验证码字体识别率明显低于 vision LLM，推荐配置 LLM。

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

- **LLM 返回非 JSON** —— `solvers/llm_vision.py` 内部会做一次重试；仍失败则抛 `SolveError`，由 pipeline 走 ddddocr 回退
- **bbox 数量少于目标字数** —— 说明 detection 漏检，记录 warning 后仍会尽力回答，但通过率会下降
- **提示词里混入标点或空格** —— `_parse_target_chars` 用 `[\u4e00-\u9fff]` 正则只抓 CJK 汉字
- **最后兜底** —— 如果所有方法都没给某个字找到 box，会把它分配到第一个未被使用的 bbox，至少保证一次可见的点击（而不是 `(0,0)` 的显式错误）
