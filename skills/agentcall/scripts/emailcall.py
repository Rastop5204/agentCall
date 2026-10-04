#!/usr/bin/env python3
"""Compatibility entrypoint for exported EmailCall commands."""
from pathlib import Path
import runpy

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).with_name("agentcall.py")), run_name="__main__")
