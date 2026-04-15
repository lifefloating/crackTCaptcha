# Icon Click Challenge

## How It Works

1. `prehandle` returns N hint icons in `fg_elem_list`
2. Download background + sprite, crop each hint icon
3. `ddddocr` detects candidate icon bboxes on background
4. Match each hint icon to best candidate via normalized correlation
5. Generate click trajectory between targets
6. Submit ordered click coordinates

## Dependencies

Requires `ddddocr` (optional extra):

```bash
uv add "crack-tcaptcha[icon-click]"
```

## Answer Format

```json
[
  {"elem_id": 1, "type": "DynAnswerType_POS", "data": "120,80"},
  {"elem_id": 2, "type": "DynAnswerType_POS", "data": "300,200"},
  {"elem_id": 3, "type": "DynAnswerType_POS", "data": "450,150"}
]
```
