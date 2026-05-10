"""
tests/opportunities/conftest.py
Activa el modo auto de pytest-asyncio sólo para esta suite, sin
afectar el resto del proyecto. Solo marca tests async, no los sync.
"""
import asyncio
import inspect
import pytest


def pytest_collection_modifyitems(config, items):
    for item in items:
        if "tests/opportunities" not in str(item.fspath):
            continue
        func = getattr(item, "function", None) or getattr(item, "obj", None)
        if func is None:
            continue
        if asyncio.iscoroutinefunction(func) or inspect.isasyncgenfunction(func):
            item.add_marker(pytest.mark.asyncio)
