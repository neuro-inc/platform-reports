ARG PYTHON_VERSION=3.8.12
ARG PYTHON_BASE=buster

FROM python:${PYTHON_VERSION} AS installer

ARG DIST_FILENAME

ENV PATH=/root/.local/bin:$PATH

COPY ${DIST_FILENAME} ${DIST_FILENAME}

RUN ls dist
RUN pip install --user ${DIST_FILENAME}

FROM python:${PYTHON_VERSION}-${PYTHON_BASE} as service

LABEL org.opencontainers.image.source = "https://github.com/neuro-inc/platform-reports"

WORKDIR /app

ENV PATH=/root/.local/bin:$PATH
ENV NP_REPORTS_API_PORT=8080

COPY --from=installer /root/.local/ /root/.local/
