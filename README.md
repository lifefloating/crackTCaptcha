# crack-tcaptcha

> Automated solver for Tencent TCaptcha — slider & icon-click challenges.
> Pure HTTP protocol, no browser automation required.

## Install

```bash
# core (slider only)
uv add crack-tcaptcha

# with icon-click support (pulls ddddocr + onnxruntime)
uv add "crack-tcaptcha[icon-click]"
```

**Prerequisites**: Node.js >= 18 (for TDC.js bridge).

```bash
cd src/crack_tcaptcha/tdc/js && npm install
```

## Quick Start

```python
from crack_tcaptcha import solve, TCaptchaType

result = solve(
    appid="2046626712",
    challenge_type=TCaptchaType.SLIDER,
    max_retries=3,
)
if result.ok:
    print(result.ticket, result.randstr)
```

## CLI

```bash
crack-tcaptcha solve --type slider --appid 2046626712 --retries 3 --json
```

## Development

```bash
uv sync --all-extras
uv run ruff check .
uv run pytest -x -ra
```

## Disclaimer

This project is for **security research, automated testing, accessibility, and academic analysis** only.
**Do not** use it for mass registration, scalping, illegal scraping, or any activity that violates
the target site's Terms of Service or applicable laws. All liability rests with the end user.

## License

GPL-3.0-or-later
