"""Global settings via pydantic-settings (env / .env / constructor)."""

from __future__ import annotations

import pathlib

from pydantic_settings import BaseSettings


class TCaptchaSettings(BaseSettings):
    model_config = {"env_prefix": "TCAPTCHA_"}

    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
    base_url: str = "https://turing.captcha.qcloud.com"
    timeout: float = 15.0
    max_retries: int = 3
    tdc_js_dir: pathlib.Path = pathlib.Path(__file__).resolve().parent / "tdc" / "js"
    tdc_timeout: float = 10.0
    proxy: str | None = None


settings = TCaptchaSettings()
