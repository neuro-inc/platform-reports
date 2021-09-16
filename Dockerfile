ARG PYTHON_VERSION=3.8.5
ARG PYTHON_BASE=buster

FROM python:${PYTHON_VERSION} AS installer

ENV PATH=/root/.local/bin:$PATH

# Separate step for requirements to speed up docker builds
COPY platform_reports.egg-info/requires.txt requires.txt
RUN python -c 'from pkg_resources import Distribution, PathMetadata;\
    dist = Distribution(metadata=PathMetadata(".", "."));\
    print("\n".join(str(r) for r in dist.requires()));\
    ' > requirements.txt
RUN pip install -U pip && pip install --user -r requirements.txt

ARG DIST_FILENAME

# Install service itself
COPY dist/${DIST_FILENAME} ${DIST_FILENAME}
RUN pip install --user $DIST_FILENAME

FROM python:${PYTHON_VERSION}-${PYTHON_BASE} as service

LABEL org.opencontainers.image.source = "https://github.com/neuro-inc/platform-reports"

WORKDIR /app

ENV PATH=/root/.local/bin:$PATH
ENV NP_REPORTS_API_PORT=8080

COPY --from=installer /root/.local/ /root/.local/
