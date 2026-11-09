# The stardelt CLI in a container: what the operator's maintenance CronJob runs
# (operator#476 slice b). No Jupyter, no Spark client; the library's base
# dependencies are enough for `stardelt maintenance run`.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir /app

# An arbitrary non-root id: the command needs no home directory and writes only
# stdout. 65532 is the nonroot id the platform's other images use.
USER 65532:65532

ENTRYPOINT ["stardelt"]
