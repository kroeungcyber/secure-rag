FROM python:3.11-slim AS builder
WORKDIR /build
# pysqlite3 has no prebuilt linux/arm64 wheel; it needs a compiler to build
# from source. Build in a dedicated stage so the runtime image stays slim.
RUN apt-get update && apt-get install -y --no-install-recommends gcc python3-dev libsqlite3-dev \
    && rm -rf /var/lib/apt/lists/*
COPY . .
RUN pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.11-slim
WORKDIR /app
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*
COPY --from=builder /build/PROGRAM.md /app/PROGRAM.md

ENV SRAG_PROGRAM_PATH=/app/PROGRAM.md
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["srag", "serve", "--host", "0.0.0.0", "--port", "8000"]
