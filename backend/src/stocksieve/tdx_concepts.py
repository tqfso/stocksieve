"""本文件读取公开通达信概念目录，一次取得成员表，成员范围按源口径标记为部分覆盖。"""

import hashlib
import re
import struct
from pathlib import Path
from typing import Any

from .errors import DomainError
from .source_policy import SourcePolicy
from .sources import SourceHTTP


def parse_concepts(content: bytes) -> dict[str, list[dict[str, str]]]:
    """按Pytdx开源BlockReader格式读取，仅接受完整块及合法六位成员，不执行远程内容。"""
    from pytdx.reader.block_reader import BlockReader, BlockReader_TYPE_GROUP
    if len(content) < 386:
        raise DomainError('DATA_NOT_AVAILABLE', '概念目录文件太短。', 503)
    count = struct.unpack_from('<H', content, 384)[0]
    if not 1 <= count <= 5000 or len(content) < 386 + count * 2813:
        raise DomainError('DATA_NOT_AVAILABLE', '概念目录结构或大小未通过校验。', 503)
    for index in range(count):
        members = struct.unpack_from('<H', content, 386 + index * 2813 + 9)[0]
        if members > 400:
            raise DomainError('DATA_NOT_AVAILABLE', '概念目录成员数量超过格式边界。', 503)
    mapping: dict[str, list[dict[str, str]]] = {}
    for block in BlockReader().get_data(bytearray(content), BlockReader_TYPE_GROUP):
        name = block['blockname'].strip()
        if not name:
            continue
        concept = {'id': 'tdx_' + hashlib.sha256(name.encode()).hexdigest()[:24], 'name': name, 'sourceCode': name}
        for code in set(block['code_list'].split(',')):
            if re.fullmatch(r'[0-9]{6}', code):
                mapping.setdefault(code, []).append(concept)
    if not mapping:
        raise DomainError('DATA_NOT_AVAILABLE', '概念目录没有可用成员。', 503)
    return mapping


class TdxConcepts:
    """公开目录每天最多重新获取一次，不逐股票抓取概念；无资料时保持未知。"""

    def __init__(self, policy: SourcePolicy, cache: Path) -> None:
        self.policy, self.cache = policy, cache

    async def read(self) -> dict[str, list[dict[str, str]]]:
        client = SourceHTTP(self.policy, self.cache)
        try:
            content, _ = await client.document('tdx', 'http://file.tdx.com.cn/fin/block_gn.dat')
            return parse_concepts(content)
        finally:
            await client.close()
