// 本文件生成请求及幂等提交编号，兼容局域网普通HTTP地址中缺少randomUUID的浏览器。

/** 优先使用原生UUID；普通HTTP环境使用随机字节生成同格式的UUID v4。 */
export function randomId(): string {
    const source = globalThis.crypto;
    if (typeof source?.randomUUID === 'function') return source.randomUUID();
    if (typeof source?.getRandomValues !== 'function') throw new Error('当前浏览器无法生成请求编号，请更新浏览器后重试。');
    const bytes = source.getRandomValues(new Uint8Array(16));
    // UUID v4版本和变体位固定，其余位保持浏览器生成的随机值。
    bytes[6] = (bytes[6]! & 0x0f) | 0x40;
    bytes[8] = (bytes[8]! & 0x3f) | 0x80;
    const text = Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('');
    return `${text.slice(0, 8)}-${text.slice(8, 12)}-${text.slice(12, 16)}-${text.slice(16, 20)}-${text.slice(20)}`;
}
