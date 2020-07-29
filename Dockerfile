ARG PYTHON_VERSION=3.8.5

FROM python:${PYTHON_VERSION} AS requirements

ARG PIP_EXTRA_INDEX_URL
ENV PATH=/root/.local/bin:$PATH

WORKDIR /app

COPY setup.py setup.py

RUN pip install --user -e . &&\
    pip uninstall -y platform-reports


# make service image
FROM python:${PYTHON_VERSION} as service

WORKDIR /app

ENV PATH=/root/.local/bin:$PATH

COPY --from=requirements /root/.local /root/.local
COPY setup.py setup.py
COPY platform_reports platform_reports

RUN pip install --user -e .

ENV NP_REPORTS_API_PORT=8080
