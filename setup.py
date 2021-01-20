from setuptools import find_packages, setup


setup_requires = ("setuptools_scm",)
install_requires = (
    "neuro_auth_client==19.11.25",
    "platform_config_client==20.10.23",
    "neuromation==20.12.7",
    "platform-logging==0.3",
    "aiohttp==3.7.3",
    "python-jose==3.2.0",
    "lark-parser==0.11.1",
    "aiobotocore==1.1.2",
    "google-api-python-client==1.12.5",
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
