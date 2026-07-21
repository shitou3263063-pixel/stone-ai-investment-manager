"""Deployment-check adapter; production execution is only ``python main.py``."""

from __future__ import annotations


def run(*, send_email: bool = True) -> str:
    from src.pipeline.unified_pipeline import run as unified_run

    return unified_run(send_email=send_email)
