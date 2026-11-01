"""本文件统一接口响应、请求追踪和异常映射，不将凭据或内部异常返回给客户端。"""

import logging
import re
from typing import Any, TypeVar
from uuid import uuid4

from aiohttp import web
from pydantic import BaseModel, ValidationError

from stocksieve.shared.errors import DomainError
from stocksieve.shared.serialization import encode

Model = TypeVar('Model', bound=BaseModel)
logger = logging.getLogger('stocksieve.http')
REQUEST_ID = web.RequestKey('requestId', str)
USER = web.RequestKey('user', dict)


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
    except Exception as error:
        # 只记录类型和约束名用于运维定位，不输出可能带口令、令牌或整行数据的异常文本。
        diagnostic = getattr(getattr(error, 'orig', None), 'diag', None)
        logger.error('接口处理失败 requestId=%s method=%s path=%s type=%s constraint=%s',
                     request_id, request.method, request.path, type(error).__name__, getattr(diagnostic, 'constraint_name', None))
        response = web.json_response({'error': {'code': 'INTERNAL_ERROR', 'message': '服务暂时无法完成请求。', 'details': {}}, 'requestId': request_id}, status=500)
    response.headers['X-Request-ID'] = request_id
    response.headers['Cache-Control'] = 'no-store'
    return response
