from setuptools import find_packages, setup


install_requires = (
    "neuro_auth_client==19.11.25",
    "neuromation==20.9.3",
    "platform-logging==0.3",
    "aiohttp==3.6.2",
    "python-jose==3.1.0",
    "lark-parser==0.9.0",
)

setup(
    name="platform_reports",
    version="1.0.0",
    url="https://github.com/neuromation/platform-reports",
    packages=find_packages(),
    python_requires=">=3.7",
    install_requires=install_requires,
    entry_points={"console_scripts": ["platform-reports=platform_reports.api:main"]},
    zip_safe=False,
)
