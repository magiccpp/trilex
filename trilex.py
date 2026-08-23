#!/usr/bin/env python3
"""Launcher: python trilex.py"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from trilex.gui import main
sys.exit(main())
