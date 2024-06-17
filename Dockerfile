ARG PY_VERSION=3.11

FROM python:${PY_VERSION}-slim-bookworm AS requirements

ENV PATH=/root/.local/bin:$PATH

# Copy to tmp folder to don't pollute home dir
RUN mkdir -p /tmp/dist
COPY dist /tmp/dist

RUN ls /tmp/dist
RUN pip install --user --find-links /tmp/dist platform-reports

FROM python:${PY_VERSION}-slim-bookworm as service

LABEL org.opencontainers.image.source = "https://github.com/neuro-inc/platform-reports"

WORKDIR /app

ENV PATH=/root/.local/bin:$PATH

COPY --from=requirements /root/.local/ /root/.local/
