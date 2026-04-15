# Slider Challenge

## How It Works

1. `prehandle` returns session + sprite layout info
2. Download background (with gap shadow) and foreground sprite
3. Crop puzzle piece from sprite using `sprite_pos` + `size_2d`
4. NCC template matching finds the gap position
5. Generate ease-in-out trajectory from `init_pos` to gap
6. Feed trajectory to TDC.js via jsdom bridge
7. Submit `ans` + `collect` + `eks` to verify endpoint

## NCC Algorithm

The puzzle piece has an alpha channel — only opaque pixels (`alpha > 128`) participate in matching.

```python
from crack_tcaptcha.slider.solver import SliderSolver

solver = SliderSolver()
target_x, target_y, ncc = solver.solve(bg_bytes, fg_bytes, piece_elem)
```

## Answer Format

```json
[{"elem_id": 1, "type": "DynAnswerType_POS", "data": "300,150"}]
```
