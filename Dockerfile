
ARG PYTHON_IMAGE=python:3.11.16-slim-trixie@sha256:e41613d42d4891e4930f79523f93f81bbc7632584ec65e36ab055f41a800b41e


FROM ${PYTHON_IMAGE} AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

COPY pyproject.toml .
COPY src/ src/
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-deps .


FROM ${PYTHON_IMAGE} AS runtime

ARG MODEL_VERSION=unknown
ARG GIT_SHA=unknown

LABEL org.opencontainers.image.title="velov-api" \
      org.opencontainers.image.description="API de prédiction de disponibilité Vélo'v" \
      org.opencontainers.image.version="${MODEL_VERSION}" \
      org.opencontainers.image.revision="${GIT_SHA}"

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MODEL_DIR=/app/models \
    LOG_LEVEL=INFO

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

COPY models/model.joblib models/metadata.json /app/models/

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --start-interval=2s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/ready', timeout=4).status == 200 else 1)"]

CMD ["uvicorn", "velov.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
