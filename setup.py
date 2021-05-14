from setuptools import find_packages, setup


setup_requires = ("setuptools_scm",)
install_requires = (
    "neuro_auth_client==21.5.8",
    "platform_config_client==21.5.9",
    "neuro-sdk==21.4.23",
    "platform-logging==21.5.7",
    "aiohttp==3.7.4",
    "python-jose==3.2.0",
    "lark-parser==0.11.2",
    "aiobotocore==1.3.0",
    "google-api-python-client==2.0.2",
    "aiozipkin==1.0.0",
    "sentry-sdk==1.0.0",
)

setup(
    name="platform_reports",
    url="https://github.com/neuromation/platform-reports",
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
