from setuptools import find_packages, setup


setup_requires = ("setuptools_scm",)
install_requires = (
    "neuro_auth_client==21.9.13.1",
    "neuro-config-client==21.9.14.1",
    "neuro-sdk==21.9.3",
    "neuro-logging==21.8.4.1",
    "aiohttp==3.7.4.post0",
    "python-jose==3.3.0",
    "lark-parser==0.12.0",
    "aiobotocore==1.4.1",
    "google-api-python-client==2.23.0",
    "aiozipkin==1.1.0",
    "sentry-sdk==1.4.2",
)

setup(
    name="platform_reports",
    url="https://github.com/neuro-inc/platform-reports",
    use_scm_version={
        "git_describe_command": "git describe --dirty --tags --long --match v*.*.*",
    },
    packages=find_packages(),
    python_requires=">=3.7",
    setup_requires=setup_requires,
    install_requires=install_requires,
    entry_points={
        "console_scripts": [
            "metrics-server=platform_reports.api:run_metrics_server",
            "prometheus-proxy=platform_reports.api:run_prometheus_proxy",
            "grafana-proxy=platform_reports.api:run_grafana_proxy",
        ]
    },
    zip_safe=False,
)
