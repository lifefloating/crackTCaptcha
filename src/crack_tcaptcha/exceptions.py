"""crack_tcaptcha exceptions."""


class TCaptchaError(Exception):
    """Base exception for all crack_tcaptcha errors."""


class NetworkError(TCaptchaError):
    """HTTP / connectivity error."""


class SolveError(TCaptchaError):
    """Failed to solve the challenge (NCC / icon matching / LLM)."""


class TDCError(TCaptchaError):
    """TDC.js execution failed."""


class PowError(TCaptchaError):
    """PoW brute-force exceeded the search limit."""


class UnsupportedCaptchaType(TCaptchaError):
    """Classifier returned 'unknown' or dispatch found no matching pipeline."""

    def __init__(self, captcha_type: str, dyn_keys: list[str]):
        super().__init__(f"unsupported captcha type {captcha_type!r}; dyn keys={dyn_keys}")
        self.captcha_type = captcha_type
        self.dyn_keys = dyn_keys
