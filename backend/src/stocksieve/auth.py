"""本文件实现密码哈希、可撤销会话及管理员用户管理，数据库不保存明文凭据。"""

import base64
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import func, select, update

from .database import Database, utc
from .errors import DomainError
from .tables import identifier, sessions, users


def hash_password(password: str) -> str:
    """使用独立随机盐和固定资源参数生成可验证的 scrypt 哈希。"""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024)
    return 'scrypt$16384$8$1$' + base64.b64encode(salt).decode() + '$' + base64.b64encode(digest).decode()


def check_password(password: str, encoded: str) -> bool:
    """固定参数并恒时比较摘要，损坏或未知的哈希格式验证失败。"""
    try:
        algorithm, n, r, p, salt, expected = encoded.split('$')
        if algorithm != 'scrypt' or (int(n), int(r), int(p)) != (16384, 8, 1):
            return False
        digest = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt, validate=True), n=int(n), r=int(r), p=int(p), dklen=32, maxmem=64 * 1024 * 1024)
        return hmac.compare_digest(digest, base64.b64decode(expected, validate=True))
    except (ValueError, TypeError):
        return False


def username(value: str) -> str:
    result = value.strip().lower()
    if not re.fullmatch(r'[a-z][a-z0-9_]{2,19}', result):
        raise DomainError('INVALID_ARGUMENT', '账号需为3～20位字母、数字或下划线，并以字母开头。')
    return result


def public_user(row: Any) -> dict[str, Any]:
    return {'id': row['id'], 'username': row['username'], 'name': row['name'],
            'role': row['role'], 'createdAt': utc(row['created_at']).isoformat()}


class Auth:
    """会话按令牌摘要查询，退出撤销当前会话，改密撤销全部会话。"""

    def __init__(self, database: Database, clock: Callable[[], datetime] | None = None) -> None:
        self.db = database
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.dummy_hash = hash_password(secrets.token_urlsafe(24))

    def create_user(self, account: str, name: str, password: str, *, admin: bool = False) -> dict[str, Any]:
        """规范账号并原子创建；管理员初始化只允许执行一次。"""
        normalized = username(account)
        if not 1 <= len(name.strip()) <= 20 or not 6 <= len(password) <= 32:
            raise DomainError('INVALID_ARGUMENT', '名称需为1～20字，密码需为6～32位。')
        encoded = hash_password(password)
        with self.db.transaction(serialized=True) as connection:
            if connection.execute(select(users.c.id).where(users.c.username == normalized)).first():
                raise DomainError('USERNAME_EXISTS', '该账号已存在。', 409)
            if admin and connection.execute(select(users.c.id).where(users.c.role == 'admin')).first():
                raise DomainError('ADMIN_EXISTS', '已有管理员，初始化命令不会修改或覆盖现有账号。', 409)
            record = dict(id=identifier(), username=normalized, name=name.strip(), password_hash=encoded,
                          role='admin' if admin else 'user', created_at=self.clock(), updated_at=self.clock(), password_changed_at=self.clock())
            connection.execute(users.insert().values(**record))
            return public_user(record)

    def login(self, account: str, password: str) -> dict[str, Any]:
        """验证后重新检查密码版本，避免并发改密后签发旧凭据会话。"""
        account = username(account)
        if not password or len(password) > 32:
            raise DomainError('INVALID_ARGUMENT', '密码必填且最长32位。')
        with self.db.transaction() as connection:
            row = connection.execute(select(users).where(users.c.username == account)).mappings().first()
        verified = check_password(password, row['password_hash'] if row else self.dummy_hash)
        if not row or not verified:
            raise DomainError('INVALID_CREDENTIALS', '账号或密码错误。', 401)
        token = secrets.token_urlsafe(32)
        expires = self.clock() + timedelta(hours=8)
        with self.db.transaction(serialized=True) as connection:
            # 密码验证后若发生并发改密，不签发基于旧密码的新会话。
            current = connection.execute(select(users).where(users.c.id == row['id'])).mappings().one()
            if current['password_hash'] != row['password_hash']:
                raise DomainError('INVALID_CREDENTIALS', '账号或密码错误。', 401)
            connection.execute(sessions.insert().values(user_id=row['id'], token_hash=hashlib.sha256(token.encode()).hexdigest(), created_at=self.clock(), expires_at=expires))
        return {'accessToken': token, 'tokenType': 'Bearer', 'expiresAt': expires.isoformat(), 'user': public_user(row)}

    def authenticate(self, header: str | None) -> dict[str, Any]:
        """只用令牌摘要查询未撤销且未过期的会话。"""
        if not header or not header.startswith('Bearer ') or not 20 <= len(header[7:]) <= 256:
            raise DomainError('UNAUTHENTICATED', '请先登录。', 401)
        digest = hashlib.sha256(header[7:].encode()).hexdigest()
        with self.db.transaction() as connection:
            row = connection.execute(select(sessions).where(sessions.c.token_hash == digest)).mappings().first()
            if not row or row['revoked_at'] is not None:
                raise DomainError('UNAUTHENTICATED', '会话已失效，请重新登录。', 401)
            if utc(row['expires_at']) <= self.clock():
                raise DomainError('TOKEN_EXPIRED', '登录已过期，请重新登录。', 401)
            user = connection.execute(select(users).where(users.c.id == row['user_id'])).mappings().one()
        return {**public_user(user), 'sessionId': row['id']}

    def logout(self, user: dict[str, Any]) -> None:
        with self.db.transaction() as connection:
            connection.execute(update(sessions).where(sessions.c.id == user['sessionId']).values(revoked_at=self.clock()))

    def change_password(self, user: dict[str, Any], old: str, new: str) -> None:
        """修改密码与撤销该用户全部会话在同一事务提交。"""
        if user['role'] != 'admin':
            raise DomainError('FORBIDDEN', '仅管理员可修改自身密码。', 403)
        if not 6 <= len(new) <= 32 or new == old:
            raise DomainError('INVALID_ARGUMENT', '新密码需为6～32位且与原密码不同。')
        with self.db.transaction(serialized=True) as connection:
            row = connection.execute(select(users).where(users.c.id == user['id'])).mappings().one()
            if not check_password(old, row['password_hash']):
                raise DomainError('CURRENT_PASSWORD_INVALID', '当前密码不正确。')
            connection.execute(update(users).where(users.c.id == user['id']).values(password_hash=hash_password(new), password_changed_at=self.clock(), updated_at=self.clock()))
            connection.execute(update(sessions).where(sessions.c.user_id == user['id'], sessions.c.revoked_at.is_(None)).values(revoked_at=self.clock()))

    def list_users(self, search: str, role: str | None, page: int, page_size: int) -> dict[str, Any]:
        predicates = []
        if search:
            escaped = search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            predicates.append(users.c.username.ilike(f'%{escaped}%', escape='\\') | users.c.name.ilike(f'%{escaped}%', escape='\\'))
        if role:
            predicates.append(users.c.role == role)
        with self.db.transaction() as connection:
            total = connection.scalar(select(func.count()).select_from(users).where(*predicates))
            rows = connection.execute(select(users).where(*predicates).order_by(users.c.created_at.desc(), users.c.id).offset((page - 1) * page_size).limit(page_size)).mappings()
            return {'items': [public_user(row) for row in rows], 'total': total, 'page': page, 'pageSize': page_size}
