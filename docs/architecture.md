# Architecture

## Three-Phase Protocol

All endpoints live under `https://turing.captcha.qcloud.com`.

```
┌──────────┐   prehandle    ┌──────────┐   getcapbysig   ┌──────────┐
│  Client   │──────────────▶│  Server   │◀───────────────│  Client   │
│           │◀──────────────│           │───────────────▶│           │
│           │  sess, pow,   │           │  bg/fg PNGs    │           │
│           │  fg_elem_list │           │                │           │
│           │               │           │                │           │
│           │   verify      │           │                │           │
│           │──────────────▶│           │                │           │
│           │◀──────────────│           │                │           │
│           │ ticket/randstr│           │                │           │
└──────────┘               └──────────┘                └──────────┘
```

### Phase 1: `cap_union_prehandle`
- Initializes session, returns `sess`, `pow_cfg`, `fg_elem_list`, `bg_elem_cfg`.
- `subsid` increments on each retry attempt.

### Phase 2: `cap_union_new_getcapbysig`
- `img_index=1` → background (672×390 RGB PNG)
- `img_index=0` → foreground sprite (682×620 RGBA PNG)

### Phase 3: `cap_union_new_verify`
- POST with `ans`, `pow_answer`, `pow_calc_time`, `collect`, `tlg`, `eks`.
- Returns `ticket` + `randstr` on success.

## NCC Template Matching

Two-phase search:
1. **Coarse**: stride=4 along `init_y` row → ~168 evaluations
2. **Fine**: ±6px X × ±5px Y → ~143 evaluations

Total ~311 vs full-image 262,080 → **842× speedup**.

## TDC.js Bridge

```
Python ──subprocess──▶ Node.js + jsdom ──eval──▶ tdc.js
                              │
                              ▼
                    {collect, eks, tlg}
```

Provider protocol allows swapping to Puppeteer if jsdom is detected.
