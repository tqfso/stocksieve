#!/usr/bin/env bash
# 本文件检查本地 Docker 资源、部署 PostgreSQL，并在空数据库执行建表与约束验证。
set -Eeuo pipefail

postgres_deploy_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
postgres_env_file="${postgres_deploy_dir}/.env"
postgres_container="stocksieve-postgres"
postgres_volume="stocksieve-postgres-data"
postgres_sql_dir="$(cd -- "${postgres_deploy_dir}/../../sql" && pwd)"

# 固定项目目录和配置文件，避免从其他目录执行时连接到不同实例。
postgres_compose() {
    docker compose --project-directory "$postgres_deploy_dir" \
        --project-name stocksieve-postgres --env-file "$postgres_env_file" \
        --file "${postgres_deploy_dir}/compose.yaml" "$@"
}

# 1. 先确认 Docker 可访问、本地镜像存在；不执行镜像下载。
[[ -f "${postgres_sql_dir}/init.sql" && -f "${postgres_sql_dir}/tests/constraints.sql" ]]
if [[ -L "$postgres_env_file" || ( -e "$postgres_env_file" && ! -f "$postgres_env_file" ) ]]; then
    printf '%s\n' '.env 必须是本目录中的普通文件，已停止。' >&2
    exit 1
fi
command -v docker >/dev/null
command -v python3 >/dev/null
docker info >/dev/null
docker compose version >/dev/null
docker image inspect postgres:15-alpine >/dev/null

# 2. 拒绝接管其他项目的同名容器，或在首次部署时自动复用已有卷。
if docker container inspect "$postgres_container" >/dev/null 2>&1; then
    postgres_existing_project="$(docker container inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$postgres_container")"
    postgres_existing_service="$(docker container inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "$postgres_container")"
    postgres_existing_image="$(docker container inspect --format '{{.Config.Image}}' "$postgres_container")"
    postgres_existing_mount="$(docker container inspect --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Type}}:{{.Name}}{{end}}{{end}}' "$postgres_container")"
    if [[ "$postgres_existing_project" != "stocksieve-postgres" || "$postgres_existing_service" != "postgres" || "$postgres_existing_image" != "postgres:15-alpine" || "$postgres_existing_mount" != "volume:${postgres_volume}" ]]; then
        printf '%s\n' '同名容器不符合本项目配置，已停止，未修改现有资源。' >&2
        exit 1
    fi
    if [[ ! -f "$postgres_env_file" ]]; then
        printf '%s\n' '已有项目容器但缺少 .env，请先恢复原连接配置，不能自动生成新密码。' >&2
        exit 1
    fi
elif docker volume inspect "$postgres_volume" >/dev/null 2>&1; then
    printf '%s\n' '发现已有同名数据卷，已停止。请核对原数据后按部署文档手动接续。' >&2
    exit 1
fi

# 3. 首次生成私有连接配置，已有文件不覆盖；密码不会输出到终端。
if [[ ! -f "$postgres_env_file" ]]; then
    python3 - "$postgres_env_file" <<'PY'
import os
import secrets
import sys

postgres_env_path = sys.argv[1]
postgres_password = secrets.token_hex(32)
postgres_env_content = (
    "STOCKSIEVE_POSTGRES_USER=stocksieve_admin\n"
    "STOCKSIEVE_POSTGRES_DB=stocksieve\n"
    "STOCKSIEVE_POSTGRES_PORT=5432\n"
    f"STOCKSIEVE_POSTGRES_PASSWORD={postgres_password}\n"
)
postgres_descriptor = os.open(postgres_env_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(postgres_descriptor, "w") as postgres_env_stream:
    postgres_env_stream.write(postgres_env_content)
PY
fi
chmod 600 "$postgres_env_file"

# 4. 校验配置后启动服务，端口冲突或健康检查失败时直接停止本次流程。
postgres_compose config --quiet
postgres_compose up -d --wait --wait-timeout 90

# 5. 先在本次专用临时库验证 SQL，避免未通过验证的结构进入目标库。
postgres_verify_db="stocksieve_verify_$(python3 -c 'import secrets; print(secrets.token_hex(8))')"
postgres_verify_created=false

# 仅清理本次成功创建的临时验证库，不接触目标数据库或已有卷。
postgres_cleanup_verification() {
    if [[ "$postgres_verify_created" == true ]]; then
        postgres_compose exec -T postgres sh -eu -c 'exec dropdb --if-exists -U "$POSTGRES_USER" "$1"' sh "$postgres_verify_db" >/dev/null 2>&1 || true
    fi
}
trap postgres_cleanup_verification EXIT
postgres_compose exec -T postgres sh -eu -c 'exec createdb -U "$POSTGRES_USER" "$1"' sh "$postgres_verify_db"
postgres_verify_created=true
postgres_compose exec -T postgres sh -eu -c 'exec psql -X -q -U "$POSTGRES_USER" -d "$1" -v ON_ERROR_STOP=1' sh "$postgres_verify_db" < "${postgres_sql_dir}/init.sql"
postgres_compose exec -T postgres sh -eu -c 'exec psql -X -q -U "$POSTGRES_USER" -d "$1" -v ON_ERROR_STOP=1' sh "$postgres_verify_db" < "${postgres_sql_dir}/tests/constraints.sql"
postgres_compose exec -T postgres sh -eu -c 'exec dropdb -U "$POSTGRES_USER" "$1"' sh "$postgres_verify_db"
postgres_verify_created=false

# 6. 验证通过后初始化目标空库，再检查 TCP 密码认证与17张表。
postgres_compose exec -T postgres sh -eu -c 'exec psql -X -q -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' < "${postgres_sql_dir}/init.sql"
postgres_compose exec -T postgres sh -eu -c 'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -X -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "SELECT current_database(), current_user, (SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname = '\''public'\'') AS table_count;"'
printf '%s\n' 'PostgreSQL 已部署并完成初始化。连接配置保存在 backend/deploy/postgres/.env。'
