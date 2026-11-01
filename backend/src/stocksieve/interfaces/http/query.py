"""本文件校验HTTP查询参数的允许字段与重复参数。"""

from multidict import MultiDictProxy

from stocksieve.shared.query import invalid


def known_query(query: MultiDictProxy[str], allowed: set[str]) -> dict[str, str]:
    for key in query:
        if key not in allowed or len(query.getall(key)) != 1:
            raise invalid(key, '存在未知或重复的查询参数。')
    return {key: value for key, value in query.items() if value != ''}
