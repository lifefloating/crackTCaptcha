# 图标点击（icon_click）

## 流程

1. `prehandle` 在 `fg_elem_list` 中返回 N 个提示图标
2. 下载背景 + sprite，逐个裁出提示图标
3. `ddddocr` 在背景上检测候选图标的 bbox
4. 把每个提示图标与候选 bbox 做归一化相关匹配，选中得分最高的
5. 按提示顺序生成点击轨迹
6. 按顺序提交点击坐标

## 依赖

需要可选 extra `icon-click`（引入 `ddddocr` + `onnxruntime`）：

```bash
uv add "crack-tcaptcha[icon-click]"
# 或在项目内开发时
uv sync --extra icon-click
```

## Answer 格式

```json
[
  {"elem_id": 1, "type": "DynAnswerType_POS", "data": "120,80"},
  {"elem_id": 2, "type": "DynAnswerType_POS", "data": "300,200"},
  {"elem_id": 3, "type": "DynAnswerType_POS", "data": "450,150"}
]
```

## 注意事项

- `elem_id` 必须按提示图标的原始顺序（1, 2, 3…）提交
- `ddddocr` 的 detection 返回 bbox 在某些场景下会漏检，保底策略是放宽阈值后重试
- 与 [word_click](word-click.md) 的区别：icon_click 的提示是 sprite 图像，word_click 的提示是 `instruction` 里的文字
