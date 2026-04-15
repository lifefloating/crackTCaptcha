# Reverse Engineering Notes

## JSONP Response Format

All prehandle responses are JSONP: `_aq_000001({...})`. Strip callback with regex before `json.loads`.

## PoW (Proof of Work)

```
Find nonce: md5(prefix + str(nonce)).hexdigest() == target_md5
```

Average nonce ~347, max ~1823, < 5ms.

## TDC.js (`__TENCENT_CHAOS_VM`)

- Custom bytecode VM, heavily obfuscated
- Collects: device fingerprint, Canvas/WebGL/Audio fingerprints, behavior trajectory
- `TDC.setData(slideData)` → `TDC.getData()` → `TDC.getInfo()`
- jsdom bypass: set `pretendToBeVisual: true`, `runScripts: "dangerously"`
- Must patch: `screen`, `innerWidth/Height`, `devicePixelRatio`, `navigator.webdriver`

## Trajectory Format

TDC expects `{elapsed_ms: "x,y"}` dict. Ease-in-out cubic with ±1px jitter passes detection.

## Known Detection Vectors

- `navigator.webdriver` — patched to false in jsdom
- Canvas fingerprint — jsdom returns blank, currently passing
- Request frequency — use proxy pool for bulk scenarios
