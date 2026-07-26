#!/usr/bin/env python3
"""
Vectrax Setup Script
Install the vx CLI tool
"""
from setuptools import setup, find_packages

setup(
    name="vectrax",
    version="1.0.0",
    description="Local-First Autonomous AI Infrastructure",
    author="Vectrax Community",
    packages=find_packages(),
    install_requires=[
        "fastapi",
        "uvicorn[standard]",
        "httpx",
        "python-multipart",
        "sqlalchemy",
        "aiosqlite",
        "python-dotenv",
        "numpy",
        "sentence-transformers",
        "torch",
        "pyyaml",
        "watchdog",
        "faiss-cpu",
    ],
    entry_points={
        "console_scripts": [
            "vx=cli.vx_main:main",
        ],
    },
    python_requires=">=3.9",
)
