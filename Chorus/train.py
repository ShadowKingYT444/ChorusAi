#!/usr/bin/env python3
"""
Hardcoded training (no CLI). Edit paths in scripts/train_autoreg_dolly.py defaults or run:

  python train.py

Requires those files under the repo (or set CHORUS_ROOT or CHORUS_TOKENIZER). See scripts/train_autoreg_dolly.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import train_autoreg_dolly

if __name__ == "__main__":
    train_autoreg_dolly.main()
