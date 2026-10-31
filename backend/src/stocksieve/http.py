"""本文件统一接口响应、请求追踪和异常映射，不将凭据或内部异常返回给客户端。"""

import json
import logging
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, TypeVar
from uuid import uuid4

from aiohttp import web
from pydantic import BaseModel, ValidationError

from .errors import DomainError

Model = TypeVar('Model', bound=BaseModel)
logger = logging.getLogger('stocksieve.http')
REQUEST_ID = web.RequestKey('requestId', str)
USER = web.RequestKey('user', dict)


def json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal) and value.is_finite():
        return float(value)
    raise TypeError('结果包含不可序列化的内容')


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, default=json_default)


def success(request: web.Request, data: Any = None, *, status: int = 200,
            headers: dict[str, str] | None = None) -> web.Response:
    if status == 204:
        return web.Response(status=204, headers=headers)
    return web.json_response({'data': data, 'requestId': request[REQUEST_ID]}, status=status, headers=headers, dumps=encode)


async def body(request: web.Request, model: type[Model]) -> Model:
    if request.content_type != 'application/json':
        raise DomainError('INVALID_ARGUMENT', '请求体需为 JSON。')
    try:
        value = await request.json()
        if not isinstance(value, dict):
            raise DomainError('INVALID_ARGUMENT', '请求体需为 JSON 对象。')
        return model.model_validate(value)
    except ValidationError as error:
        fields = [{'field': '.'.join(map(str, item['loc'])), 'message': item['msg']} for item in error.errors(include_input=False, include_context=False)]
        raise DomainError('INVALID_ARGUMENT', '请求参数无效。', details={'fields': fields}) from error
    except (ValueError, UnicodeDecodeError) as error:
        raise DomainError('INVALID_ARGUMENT', 'JSON 格式无效。') from error


@web.middleware
async def envelope(request: web.Request, handler: Any) -> web.StreamResponse:
    request_id = request.headers.get('X-Request-ID', '')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', request_id):
        request_id = 'req_' + uuid4().hex
    request[REQUEST_ID] = request_id
    try:
        response = await handler(request)
    except DomainError as error:
        response = web.json_response({'error': {'code': error.code, 'message': error.message, 'details': error.details}, 'requestId': request_id}, status=error.status, dumps=encode)
    except web.HTTPException as error:
        response = web.json_response({'error': {'code': 'HTTP_ERROR', 'message': error.reason, 'details': {}}, 'requestId': request_id}, status=error.status)
        if 'Allow' in error.headers:
            response.headers['Allow'] = error.headers['Allow']
    except Exception:
        logger.error('接口处理失败 requestId=%s method=%s path=%s', request_id, request.method, request.path)
        response = web.json_response({'error': {'code': 'INTERNAL_ERROR', 'message': '服务暂时无法完成请求。', 'details': {}}, 'requestId': request_id}, status=500)
    response.headers['X-Request-ID'] = request_id
    response.headers['Cache-Control'] = 'no-store'
    return response
