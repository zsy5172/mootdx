FROM python:3.11-slim

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /workspace

RUN python -m pip install --upgrade pip
RUN pip install --no-cache-dir "mootdx==0.11.7" "tdxpy==0.2.7"

COPY compat /opt/compat

ENV PYTHONPATH=/opt

ENTRYPOINT ["python", "-m", "compat.runners.run_api_capture"]
