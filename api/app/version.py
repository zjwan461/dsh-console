"""Single source of truth for the console version.

`pyproject.toml` reads it (`[tool.setuptools.dynamic]`), the FastAPI app reports
it, and `/system/health` returns it. Keeping one literal is what makes an issue
report, an image tag and the health endpoint agree.
"""

__version__ = "0.1.3"

__all__ = ["__version__"]