#!/usr/bin/env python3
"""
Hardcoded training entrypoint (no CLI required).

Uses defaults from train_autoreg_dolly.py (paths under repo root). Run:

  python scripts/train.py

Or from repo root after copying: python train.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import train_autoreg_dolly

if __name__ == "__main__":
    train_autoreg_dolly.main()
