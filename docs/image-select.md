# 图像选择（image_select）

即 `click_image_uncheck` 子类型：背景图被划分成若干区域（通常 6 格），要求选出 `instruction` 所描述内容所在的那一格。

## 流程

1. `prehandle` 返回 `instruction`（自然语言题面）、`bg_elem_cfg`（背景图尺寸）、`select_regions`（若干 `{id, range: [x1,y1,x2,y2]}`）
2. 下载背景图
3. **LLM vision** —— 把背景图 + 每个 region 的坐标 + `instruction` 一起发给 LLM，要求返回 `{"region_id": N}` JSON
4. 取中选 region 的中心点，生成点击轨迹
5. 通过 TDC 桥取 `collect` / `eks`
6. POST `verify`，`ans` 用 `DynAnswerType_UC` 类型

详见 `src/crack_tcaptcha/pipelines/image_select.py`。

## LLM 配置

求解器实现在 `src/crack_tcaptcha/solvers/llm_vision.py`（`match_region` 函数），和 word_click 共用同一套配置 —— 见 [word-click.md 的 LLM 配置小节](word-click.md#llm-配置)。

与 word_click 不同点：

- 输入图片不画红框；region 坐标以文本形式列在 prompt 里
- 模型只需要回答一个整数 `region_id`，输出 token 需求低（`max_tokens=64`）
- 没有 OCR 回退 —— LLM 配置缺失会直接抛 `SolveError`

## Answer 格式

```json
[{"elem_id": "", "type": "DynAnswerType_UC", "data": "3"}]
```

注意：`elem_id` 是空串，`data` 是被选中的 `region_id`（字符串），这是 `DynAnswerType_UC` 的约定，和 POS 类型不同。

## 常见坑

- **LLM 返回的整数超出 `1..N`** —— `_extract_region_id` 会先尝试 JSON 解析，失败后回退到"抽取第一个落在合法范围内的整数"，再失败才抛异常
- **题面带引号或 smart quotes** —— `_strip_instruction` 会剥掉首尾的 `“”"'` 和空白
- **region 顺序** —— 以 prehandle 返回的 `select_regions` 为准，不假设左上→右下的扫描顺序
