from setuptools import setup, find_packages
import pathlib
import re

# Read version from package without importing it
here = pathlib.Path(__file__).parent.resolve()
version_match = re.search(
    r'^__version__ = ["\']([^"\']+)["\']',
    (here / "xmu_rollcall" / "__init__.py").read_text(encoding="utf-8"),
    re.M,
)
_version = version_match.group(1) if version_match else "unknown"

# Read the contents of README file
long_description = (here / "README.md").read_text(encoding="utf-8")

setup(
    name="xmu-rollcall-cli",
    version=_version,
    packages=find_packages(),
    include_package_data=True,

    # Metadata
    author="KrsMt",
    author_email="krsmt0113@gmail.com",  # 建议填写真实邮箱
    description="XMU WeChat Bot Bundle - Automated TronClass answering through WeChat commands",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/KrsMt-0113/XMU-Rollcall-Bot",
    project_urls={
        "Bug Reports": "https://github.com/KrsMt-0113/XMU-Rollcall-Bot/issues",
        "Source": "https://github.com/KrsMt-0113/XMU-Rollcall-Bot",
    },

    # Requirements
    python_requires=">=3.9",
    install_requires=[
        "requests",
        "pycryptodome",
        "xmulogin",
        "wechatbot-sdk",
    ],

    # Entry points
    entry_points={
        "console_scripts": [
            "xmu-wechat-bot=xmu_rollcall.wechat_bot:main",
        ],
    },

    # Classifiers
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Education",
        "Intended Audience :: End Users/Desktop",
        "Topic :: Education",
        "Topic :: Utilities",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Operating System :: OS Independent",
        "Environment :: Console",
    ],

    # Keywords
    keywords="xmu wechatbot tronclass rollcall automation",

    # License
    license="MIT",
)
