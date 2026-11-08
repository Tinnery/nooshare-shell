"""Source-independent security semantics."""

def infer_exchange(stock_code: str) -> str:
    """Infer exchange from a normalized stock code, not a source suffix."""
    code = stock_code.strip()
    if code.startswith(("60", "68")):
        return "sh"
    if code.startswith(("00", "30")):
        return "sz"
    if code.startswith(("8", "4", "9")):
        return "bj"
    return "unknown"
