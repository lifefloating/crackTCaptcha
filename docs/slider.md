# 滑块（slider）

## 流程

1. `prehandle` 返回会话、`pow_cfg` 和前景 sprite 布局信息
2. 下载背景（含缺口阴影）和前景 sprite
3. 按 `sprite_pos` + `size_2d` 从 sprite 裁出拼图块
4. NCC 模板匹配在背景上定位缺口
5. 基于 `init_pos` → 缺口坐标生成缓入缓出（ease-in-out）轨迹
6. 通过 jsdom 桥把轨迹喂给 TDC.js，取出 `collect` / `eks`
7. POST 到 `verify`，提交 `ans` + `collect` + `eks`

## NCC 模板匹配算法

拼图块带 alpha 通道 —— 只有不透明像素（`alpha > 128`）参与匹配。

采用两阶段搜索：

1. **粗搜**：沿 `init_y` 行以 stride=4 扫描 → 约 168 次评估
2. **细搜**：在粗搜最大值附近 ±6 像素 X × ±5 像素 Y 精搜 → 约 143 次评估

合计 **约 311 次**，相比全图 `672 × 390 = 262 080` 次评估实现 **约 842× 加速**。

```python
from crack_tcaptcha.pipelines.slide import SliderSolver

solver = SliderSolver()
target_x, target_y, ncc = solver.solve(bg_bytes, fg_bytes, piece_elem)
```

## Answer 格式

```json
[{"elem_id": 1, "type": "DynAnswerType_POS", "data": "300,150"}]
```

## 注意事项

- 背景图与拼图块都必须先转换为 numpy 数组再匹配，注意 RGB / RGBA 的通道顺序
- 轨迹起点不是 `(0,0)`，而是 `init_pos`（前端渲染时滑块已有的起点）
- 轨迹加 ±1 像素抖动有助于通过检测，详见 [reverse-notes.md](reverse-notes.md)
