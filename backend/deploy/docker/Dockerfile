# 本文件基于用户指定的本机 Ubuntu 镜像构建后端，uv/Python 由部署脚本准备。
ARG STOCKSIEVE_BASE_IMAGE=ubuntu:24.04
FROM ${STOCKSIEVE_BASE_IMAGE}
HEALTHCHECK NONE

USER root
ENV UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_CACHE_DIR=/tmp/uv-cache \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    SSL_CERT_FILE=/opt/stocksieve-cacert.pem \
    PYTHONTZPATH=/opt/stocksieve-zoneinfo \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    STOCKSIEVE_STATE_DIR=/var/lib/stocksieve

# 只带入运行工具和公开CA，不复制宿主机虚拟环境、业务数据或凭据。
COPY .docker-build/uv /usr/local/bin/uv
COPY .docker-build/python/ /opt/python/
COPY .docker-build/cacert.pem /opt/stocksieve-cacert.pem
COPY .docker-build/zoneinfo/ /opt/stocksieve-zoneinfo/
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-python-downloads \
    && uv run --frozen --no-dev --no-sync python -c "import psycopg, aiohttp, pydantic, sqlalchemy, ssl, hashlib; from py_mini_racer import py_mini_racer; assert hasattr(hashlib, 'scrypt'); assert py_mini_racer.MiniRacer().eval('1+1') == 2" \
    && rm -rf /tmp/uv-cache

COPY src/ ./src/
COPY skills/ ./skills/
COPY assets/ ./assets/
COPY .docker-build/web/ ./web/
COPY run.py ./run.py
COPY deploy/docker/http_check.py ./http_check.py
RUN groupadd --gid 10001 stocksieve \
    && useradd --uid 10001 --gid 10001 --no-create-home --no-log-init --home-dir /var/lib/stocksieve --shell /bin/bash stocksieve \
    && mkdir -p /var/lib/stocksieve && chown 10001:10001 /var/lib/stocksieve
USER stocksieve:stocksieve
ENTRYPOINT ["/usr/local/bin/uv", "run", "--frozen", "--no-dev", "--no-sync", "python", "run.py"]
EXPOSE 8000
CMD ["api", "--host", "0.0.0.0", "--port", "8000"]
