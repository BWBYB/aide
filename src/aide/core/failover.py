"""跨厂商切换：中立化请求，丢弃厂商私有凭证（error-recovery:61）。"""

from __future__ import annotations


#: 厂商私有块/凭证：切换厂商时一律丢弃（error-recovery:61）
VENDOR_PRIVATE_KEYS = frozenset(
    {"thinking_signature", "thoughtSignature", "reasoning_content", "_raw", "vendor"}
)


def neutralize_request(request: dict) -> dict:
    """把请求转成中立格式：丢弃厂商私有凭证，其余原样保留。

    前缀（messages[0] 与 tools）必须逐字节不变，否则切厂后缓存全失效。
    """

    def scrub(value):
        if isinstance(value, dict):
            return {k: scrub(v) for k, v in value.items() if k not in VENDOR_PRIVATE_KEYS}
        if isinstance(value, list):
            return [scrub(item) for item in value]
        return value

    return scrub(request)


