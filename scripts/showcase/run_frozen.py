#!/usr/bin/env python3
"""Run a Python script as __main__ with the clock travelled to a fixed instant (ticking).

Usage: python run_frozen.py <utc-iso-time> <script.py> [script args...]
"""
import os
import runpy
import sys
from datetime import datetime, timezone

import time_machine

when, script, *args = sys.argv[1:]
sys.argv = [script, *args]
# Mimic `python script.py`: the script's own folder comes first on sys.path.
sys.path.insert(0, os.path.dirname(os.path.abspath(script)))
dest = datetime.fromisoformat(when).replace(tzinfo=timezone.utc)
with time_machine.travel(dest, tick=True):
    runpy.run_path(script, run_name="__main__")
