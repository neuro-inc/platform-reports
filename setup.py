from setuptools import find_packages, setup


install_requires = (
    "neuro_auth_client==19.11.25",
    "neuromation==20.9.3",
    "platform-logging==0.3",
    "aiohttp==3.6.2",
    "multidict==4.7.6",
    "python-jose==3.1.0",
    "lark-parser==0.9.0",
    "aiobotocore==1.1.2",
)

setup(
    name="platform_reports",
    version="1.0.0",
    url="https://github.com/neuromation/platform-reports",
    packages=find_packages(),
    python_requires=">=3.7",
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
