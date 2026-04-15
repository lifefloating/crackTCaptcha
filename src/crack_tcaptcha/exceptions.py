"""crack_tcaptcha exceptions."""


class TCaptchaError(Exception):
    """Base exception for all crack_tcaptcha errors."""


class NetworkError(TCaptchaError):
    """HTTP / connectivity error."""


class SolveError(TCaptchaError):
    """Failed to solve the challenge (NCC / icon matching)."""


class TDCError(TCaptchaError):
    """TDC.js execution failed."""


class PowError(TCaptchaError):
    """PoW brute-force exceeded the search limit."""
