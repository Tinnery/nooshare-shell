"""
gbbq 除权除息文件解密与解析。

二进制布局（每条记录，加密态 29 字节）：
  [0:8]   — 加密块 1（解密后：4 字节未使用 + 4 字节股票代码 null 终止）
  [8:16]  — 加密块 2（解密后：4 字节日期 + 1 字节类别 + 3 字节 c1 前 3 字节）
  [16:24] — 加密块 3（解密后：1 字节 c1 尾 + 4 字节 c2 + 3 字节 c3 前 3 字节）
  [24:29] — 明文（1 字节 c3 尾 + 4 字节 c4）

解密使用 4176 字节 XOR 查表密钥（_hexkeys.py）。
"""

from __future__ import annotations

import os
import struct
from pathlib import Path
from typing import List, Dict

from .models import GbbqRecord


def _load_hex_keys() -> bytes:
    """
    加载 gbbq 解密密钥。

    从环境变量 TONG_GZZX_KEY_HEX（hex 字符串）读取。
    """
    hex_str = os.environ.get("TONG_GZZX_KEY_HEX", "").strip()
    if not hex_str:
        raise ValueError("环境变量 TONG_GZZX_KEY_HEX 未设置，请在 .env 中配置")
    try:
        keys = bytes.fromhex(hex_str)
    except ValueError as exc:
        raise ValueError("TONG_GZZX_KEY_HEX must contain valid hexadecimal data") from exc
    if len(keys) != 4176:
        raise ValueError(
            "TONG_GZZX_KEY_HEX must decode to exactly 4176 bytes"
        )
    return keys


def _read_u32(data: bytes, offset: int) -> int:
    """从 bytes 中读取 LittleEndian uint32。"""
    return int.from_bytes(data[offset:offset + 4], "little")


def decrypt_block_to_buf(keys: bytes, encrypted: bytes) -> bytes:
    """
    解密 8 字节密文块，返回 8 字节明文。
    精确翻译 Go ticker-tong/tickers/tong/gbbq.go decryptBlockToBuf。

    算法：16 轮 Feistel 结构，每轮 4 次 S-box 查表（每次读 4 字节 uint32），
    字节映射顺序：byte2→0x448(ASSIGN), byte3→0x48(ADD),
                  byte1→0x848(XOR),   byte0→0xC48(ADD)。
    """
    dst = bytearray(8)
    M = 0xFFFFFFFF

    # 辅助函数：从 keys 中读取 uint32（LittleEndian）
    def ku32(off: int) -> int:
        return int.from_bytes(keys[off:off + 4], "little")

    # 初始 XOR 值
    eax = ku32(0x44)
    A = struct.unpack_from("<I", encrypted, 0)[0]
    B = struct.unpack_from("<I", encrypted, 4)[0]

    num = (eax ^ A) & M
    numold = B

    # 16 轮迭代，j 从 0x40 递减到 4（步长 4）
    j = 0x40
    while j >= 4:
        # Step 1: byte2 (bits 16-23) → table 0x448, ASSIGN
        ebx = (num & 0xFF0000) >> 16
        eax = ku32(ebx * 4 + 0x448)

        # Step 2: byte3 (bits 24-31) → table 0x48, ADD
        ebx = (num >> 24) & 0xFF
        eax = (eax + ku32(ebx * 4 + 0x48)) & M

        # Step 3: byte1 (bits 8-15) → table 0x848, XOR
        ebx = (num & 0xFF00) >> 8
        eax = eax ^ ku32(ebx * 4 + 0x848)

        # Step 4: byte0 (bits 0-7) → table 0xC48, ADD
        ebx = num & 0xFF
        eax = (eax + ku32(ebx * 4 + 0xC48)) & M

        # Round key XOR
        eax = eax ^ ku32(j)

        # Feistel 交换
        temp = num
        num = (numold ^ eax) & M
        numold = temp

        j -= 4

    # 后处理：numold ^= keys[0:4]
    numold = (numold ^ ku32(0)) & M

    struct.pack_into("<I", dst, 0, numold)
    struct.pack_into("<I", dst, 4, num)

    return bytes(dst)


def symbol_from_code(code: str) -> str:
    """
    股票代码 → 交易所前缀.symbol 格式。
    规则：6 开头 → sh，0/3 开头 → sz，4/8 开头 → bj，其他默认 sz。
    """
    if not code:
        return ""
    c = code[0]
    if c == "6":
        return f"sh{code}"
    elif c in ("0", "3"):
        return f"sz{code}"
    elif c in ("4", "8"):
        return f"bj{code}"
    else:
        return f"sz{code}"


def decrypt_gbbq(gbbq_path: str | Path) -> List[GbbqRecord]:
    """
    解密并解析 gbbq 除权除息文件。

    文件格式：
      - 头部 4 字节：记录数（uint32 LE）
      - 每条 29 字节：3×8 字节加密块 + 5 字节明文

    返回 GbbqRecord 列表。
    """
    gbbq_path = Path(gbbq_path)
    data = gbbq_path.read_bytes()

    if len(data) < 4:
        return []

    count = struct.unpack_from("<I", data, 0)[0]
    offset = 4
    records: List[GbbqRecord] = []
    keys = _load_hex_keys()

    for _ in range(count):
        if offset + 29 > len(data):
            break

        raw = data[offset:offset + 29]
        offset += 29

        # 解密 3 个 8 字节块
        decrypted = bytearray(24)
        decrypted[0:8] = decrypt_block_to_buf(keys, raw[0:8])
        decrypted[8:16] = decrypt_block_to_buf(keys, raw[8:16])
        decrypted[16:24] = decrypt_block_to_buf(keys, raw[16:24])

        # 剩余 5 字节为明文
        decrypted_full = bytes(decrypted) + raw[24:29]

        # 解析字段
        # [0] 未使用
        # [1:8] 股票代码（null 终止字符串）
        code_bytes = decrypted_full[1:8]
        stock_code = code_bytes.split(b"\x00")[0].decode("ascii", errors="replace")

        # [8:12] 日期
        date = struct.unpack_from("<I", decrypted_full, 8)[0]

        # [12] 类别
        category = decrypted_full[12]

        # [13:17] c1 (float32 LE) — 分红 (dividend)
        c1 = struct.unpack_from("<f", decrypted_full, 13)[0]

        # [17:21] c2 (float32 LE) — 配股价 (rights price)
        c2 = struct.unpack_from("<f", decrypted_full, 17)[0]

        # [21:25] c3 (float32 LE) — 送股 (bonus shares)
        c3 = struct.unpack_from("<f", decrypted_full, 21)[0]

        # [25:29] c4 (float32 LE) — 转增 (rights shares)
        c4 = struct.unpack_from("<f", decrypted_full, 25)[0]

        symbol = symbol_from_code(stock_code)

        records.append(GbbqRecord(
            category=category,
            stock_code=stock_code,
            symbol=symbol,
            date=date,
            c1=c1,
            c2=c2,
            c3=c3,
            c4=c4,
        ))

    return records


def build_gbbq_index(records: List[GbbqRecord]) -> Dict[str, List[GbbqRecord]]:
    """
    按 symbol 分组 gbbq 记录，每组内按 date 升序排序。
    返回 {symbol: [records...]}。
    """
    index: Dict[str, List[GbbqRecord]] = {}
    for r in records:
        index.setdefault(r.symbol, []).append(r)
    for sym in index:
        index[sym].sort(key=lambda r: r.date)
    return index
