ARG PYTHON_BASE_IMAGE=python:3.12-slim
FROM ${PYTHON_BASE_IMAGE}

WORKDIR /app

ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=
ARG PIP_DEFAULT_TIMEOUT=180

COPY api/requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir --retries 10 --timeout ${PIP_DEFAULT_TIMEOUT} \
    --index-url ${PIP_INDEX_URL} ${PIP_TRUSTED_HOST:+--trusted-host ${PIP_TRUSTED_HOST}} \
    -r /app/requirements.txt

COPY api /app/api
COPY analysis /app/analysis
COPY metadata /app/metadata
COPY processing /app/processing

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
