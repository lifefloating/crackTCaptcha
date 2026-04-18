"""Global settings via pydantic-settings (env / .env / constructor)."""

from __future__ import annotations

import pathlib

from pydantic_settings import BaseSettings, SettingsConfigDict


class TCaptchaSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TCAPTCHA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/147.0.0.0 Safari/537.36"
    )
    base_url: str = "https://t.captcha.qq.com"
    timeout: float = 15.0
    max_retries: int = 3
    tdc_js_dir: pathlib.Path = pathlib.Path(__file__).resolve().parent / "tdc" / "js"
    tdc_timeout: float = 60.0
    tdc_debug: bool = False
    tdc_node_path: str = "node"
    proxy: str | None = None

    # LLM vision solver (used by image_select pipeline)
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = "gpt-5.4"
    llm_timeout: float = 30.0


settings = TCaptchaSettings()
