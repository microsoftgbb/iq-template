FROM python:3.13-slim

ENV PYTHONNOUSERSITE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt ./
RUN python -m venv /app/.venv \
    && /app/.venv/bin/pip install --no-cache-dir --require-hashes --only-binary=:all: -r requirements.txt

COPY main.py instructions.md ./
# Compile once at build time so a cold session does not pay for it.
RUN /app/.venv/bin/python -m compileall -q /app/main.py /app/.venv/lib

EXPOSE 8088
CMD ["/app/.venv/bin/python", "main.py"]
