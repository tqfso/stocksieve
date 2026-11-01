#!/usr/bin/env bash
# 本文件保留数据库首次部署入口，实际使用唯一的backend/compose.yaml和backend/.env。
set -Eeuo pipefail
postgres_deploy_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
postgres_backend_root="$(cd -- "${postgres_deploy_dir}/../.." && pwd)"
exec uv run --project "$postgres_backend_root" --frozen python \
    "${postgres_backend_root}/deploy/docker/deploy.py" --database-only
