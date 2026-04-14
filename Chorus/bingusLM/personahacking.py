"""
PersonaGPT2 (GPT-2 + MiniLM persona + PersonaHead) metrics on
``google/Synthetic-Persona-Chat``.

**Runs real work:** loads the HF dataset (same parsing as ``train_persona_gpt2.py``),
batches examples, encodes personas with MiniLM, forward-passes ``PersonaGPT2``, and prints
mean CE / perplexity / argmax accuracy for the **single next-token** target per row
(the last token of the assistant reply — same objective as training).

Uses **CUDA when available**, otherwise CPU (smaller defaults).

Run the evaluator directly (always prints metrics):

  python tests/test_persona_gpt2_synthetic_persona_chat.py

Or with pytest (``-s`` to see prints):

  pip install torch transformers datasets sentence-transformers pytest
  pytest tests/test_persona_gpt2_synthetic_persona_chat.py -v -s

Env:

  DISTLM_PERSONA_GPT2_SPLIT=validation
  DISTLM_PERSONA_GPT2_EVAL_ROWS=64
  DISTLM_PERSONA_GPT2_GPT2=gpt2
  DISTLM_PERSONA_GPT2_MINILM=sentence-transformers/all-MiniLM-L6-v2
  DISTLM_PERSONA_GPT2_NUM_BLOCKS=5
  DISTLM_PERSONA_GPT2_BATCH_SIZE=4
  DISTLM_PERSONA_GPT2_MAX_LENGTH=256
  DISTLM_PERSONA_GPT2_DEVICE=auto|cuda|cpu   # default auto
  DISTLM_PERSONA_GPT2_FREEZE_GPT2=1           # default 1 (faster eval)
  HF_CACHE=/path                              # optional; default <repo>/hf_cache
  DISTLM_PERSONA_GPT2_CHECKPOINT=/path.pt     # optional; overrides default below
  DISTLM_PERSONA_GPT2_SKIP_DEFAULT_CHECKPOINT=1   # set to skip loading ``out/persona_gpt2.pt`` if present
  DISTLM_SCRIPTS=/path/to/dir                 # dir containing ``train_persona_gpt2.py`` (if not auto-found)
  DISTLM_REPO=/path/to/repo                   # alternative: uses ``<repo>/scripts``

``train_persona_gpt2.py`` is resolved from env, ``<cwd>``, the **same directory as this test file**,
``<repo>/scripts``, etc. (so you can place ``train_persona_gpt2.py`` beside your driver script in ``/workspace``).

If ``DISTLM_PERSONA_GPT2_CHECKPOINT`` is unset, the evaluator loads the first file that exists:
``<cwd>/out/persona_gpt2.pt``, then ``<repo>/out/persona_gpt2.pt`` (same default as ``train_persona_gpt2.py --out``).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def _candidate_script_dirs() -> list[Path]:
    """Paths that may contain ``train_persona_gpt2.py`` (first match wins)."""
    here = Path(__file__).resolve()
    cwd = Path.cwd().resolve()
    raw: list[Path] = []

    env_scripts = os.environ.get("DISTLM_SCRIPTS", "").strip()
    if env_scripts:
        raw.append(Path(env_scripts).expanduser().resolve())

    env_repo = os.environ.get("DISTLM_REPO", "").strip()
    if env_repo:
        raw.append(Path(env_repo).expanduser().resolve() / "scripts")

    # tests/test_*.py in repo → <repo>/scripts
    raw.append(here.parent.parent / "scripts")
    # e.g. /workspace/secondtest.py next to scripts/ → /workspace/scripts
    raw.append(here.parent / "scripts")
    # same directory as this file (e.g. /workspace/foo.py and /workspace/train_persona_gpt2.py)
    raw.append(here.parent)
    raw.append(cwd / "scripts")
    raw.append(cwd)

    seen: set[Path] = set()
    out: list[Path] = []
    for p in raw:
        try:
            r = p.resolve()
        except OSError:
            continue
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def _import_train_script():
    for d in _candidate_script_dirs():
        mod_path = d / "train_persona_gpt2.py"
        if not mod_path.is_file():
            continue
        if str(d) not in sys.path:
            sys.path.insert(0, str(d))
        import train_persona_gpt2 as tpg  # noqa: PLC0415

        return tpg

    hint = (
        "Could not find train_persona_gpt2.py. Put it in the same directory as this file, "
        "or under scripts/ next to it, or set DISTLM_SCRIPTS=/path/to/dir "
        "(directory containing train_persona_gpt2.py) or DISTLM_REPO=/path/to/DistLM."
    )
    raise ImportError(hint)


def _resolve_device():
    import torch

    raw = os.environ.get("DISTLM_PERSONA_GPT2_DEVICE", "auto").strip().lower()
    if raw == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("DISTLM_PERSONA_GPT2_DEVICE=cuda but CUDA is not available")
        return torch.device("cuda")
    if raw == "cpu":
        return torch.device("cpu")
    if raw == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    raise ValueError(f"DISTLM_PERSONA_GPT2_DEVICE must be auto|cuda|cpu, got {raw!r}")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _resolve_checkpoint_path(
    *,
    explicit: str | None,
) -> str | None:
    """
    Training script default: ``--out out/persona_gpt2.pt``.
    Non-empty ``explicit`` or ``DISTLM_PERSONA_GPT2_CHECKPOINT`` wins; otherwise use first existing default path.
    Pass ``explicit=""`` to force no checkpoint (skip env and defaults).
    """
    if explicit is not None:
        if not str(explicit).strip():
            return None
        return str(explicit).strip()

    env = os.environ.get("DISTLM_PERSONA_GPT2_CHECKPOINT", "").strip()
    if env:
        return env

    if _env_bool("DISTLM_PERSONA_GPT2_SKIP_DEFAULT_CHECKPOINT", False):
        return None

    here = Path(__file__).resolve()
    candidates = [
        Path.cwd() / "out" / "persona_gpt2.pt",
        here.parent.parent / "out" / "persona_gpt2.pt",
    ]
    for p in candidates:
        try:
            r = p.resolve()
        except OSError:
            continue
        if r.is_file():
            return str(r)
    return None


def run_persona_gpt2_eval(
    *,
    split: str | None = None,
    max_rows: int | None = None,
    gpt2_name: str | None = None,
    minilm_name: str | None = None,
    num_blocks: int | None = None,
    batch_size: int | None = None,
    max_length: int | None = None,
    freeze_gpt2: bool | None = None,
    checkpoint_path: str | None = None,
    device: Any = None,
    quiet: bool = False,
) -> dict[str, Any]:
    """
    Return dict with mean_token_ce, perplexity, token_accuracy, total_tokens_scored, device, etc.
    Each row contributes **one** scored token (training objective). Prints JSON unless ``quiet=True``.
    """
    import torch
    import torch.nn.functional as F

    tpg = _import_train_script()

    split = split or os.environ.get("DISTLM_PERSONA_GPT2_SPLIT", "validation")
    gpt2_name = gpt2_name or os.environ.get("DISTLM_PERSONA_GPT2_GPT2", "gpt2")
    minilm_name = minilm_name or os.environ.get(
        "DISTLM_PERSONA_GPT2_MINILM", "sentence-transformers/all-MiniLM-L6-v2"
    )
    max_length = max_length if max_length is not None else int(
        os.environ.get("DISTLM_PERSONA_GPT2_MAX_LENGTH", "256")
    )
    if num_blocks is None:
        nb = os.environ.get("DISTLM_PERSONA_GPT2_NUM_BLOCKS", "").strip()
        num_blocks = int(nb) if nb else 5

    dev = device or _resolve_device()
    if max_rows is None:
        env_rows = os.environ.get("DISTLM_PERSONA_GPT2_EVAL_ROWS", "").strip()
        if env_rows:
            max_rows = int(env_rows)
        else:
            max_rows = 128 if dev.type == "cuda" else 32

    if batch_size is None:
        env_bs = os.environ.get("DISTLM_PERSONA_GPT2_BATCH_SIZE", "").strip()
        if env_bs:
            batch_size = int(env_bs)
        else:
            batch_size = 8 if dev.type == "cuda" else 2

    if freeze_gpt2 is None:
        freeze_gpt2 = _env_bool("DISTLM_PERSONA_GPT2_FREEZE_GPT2", True)

    ck = _resolve_checkpoint_path(explicit=checkpoint_path)

    hf_cache = tpg.setup_hf_cache()
    tok_kw = {"cache_dir": str(hf_cache / "transformers")}
    tokenizer = tpg.GPT2Tokenizer.from_pretrained(gpt2_name, **tok_kw)
    tokenizer.pad_token = tokenizer.eos_token

    datasets_cache = hf_cache / "datasets"
    ds = tpg.PersonaChatDataset(
        split,
        tokenizer,
        max_length=max_length,
        max_examples=max_rows,
        datasets_cache=datasets_cache,
    )
    if len(ds) == 0:
        raise RuntimeError(
            f"No examples parsed for split={split!r}; check dataset schema and cache at {datasets_cache}"
        )

    model = tpg.PersonaGPT2(
        gpt2_name=gpt2_name,
        minilm_name=minilm_name,
        num_blocks=num_blocks,
        freeze_gpt2=freeze_gpt2,
        hf_cache=hf_cache,
    ).to(dev)
    model.eval()

    if ck:
        try:
            state = torch.load(ck, map_location=dev, weights_only=True)
        except TypeError:
            state = torch.load(ck, map_location=dev)
        if isinstance(state, dict) and "model_state" in state:
            model.load_state_dict(state["model_state"], strict=True)
        else:
            model.load_state_dict(state, strict=True)

    pad_id = tokenizer.pad_token_id
    collate = lambda b: tpg.collate_fn(b, pad_id)

    total_ce_sum = 0.0
    total_tok = 0
    total_correct = 0

    with torch.no_grad():
        for start in range(0, len(ds), batch_size):
            batch = [ds[i] for i in range(start, min(start + batch_size, len(ds)))]
            b = collate(batch)
            input_ids = b["input_ids"].to(dev)
            attention_mask = b["attention_mask"].to(dev)
            target_ids = b["target_ids"].to(dev)
            personas = b["personas"]

            persona_emb = model.encode_personas(personas, dev)
            logits = model(input_ids, attention_mask, persona_emb)
            loss = F.cross_entropy(logits.float(), target_ids, reduction="sum")
            total_ce_sum += loss.item()
            n = target_ids.numel()
            total_tok += n

            pred = logits.argmax(dim=-1)
            total_correct += int((pred == target_ids).sum().item())

    mean_ce = total_ce_sum / max(total_tok, 1)
    ppl = float(torch.exp(torch.tensor(mean_ce)))
    tok_acc = total_correct / max(total_tok, 1)

    out = {
        "model": "PersonaGPT2",
        "gpt2_name": gpt2_name,
        "minilm_name": minilm_name,
        "num_blocks": num_blocks,
        "split": split,
        "rows_evaluated": len(ds),
        "max_length": max_length,
        "batch_size": batch_size,
        "freeze_gpt2": freeze_gpt2,
        "checkpoint": ck,
        "device": str(dev),
        "total_tokens_scored": total_tok,
        "mean_token_ce": mean_ce,
        "perplexity": ppl,
        "token_accuracy": tok_acc,
    }
    if not quiet:
        print(json.dumps(out, indent=2), flush=True)
    return out


def test_persona_gpt2_metrics_synthetic_persona_chat() -> None:
    """End-to-end: forward pass and metrics in a sane range (untrained head unless checkpoint set)."""
    import os
    import pytest

    pytest.importorskip("torch")
    pytest.importorskip("transformers")
    pytest.importorskip("datasets")
    pytest.importorskip("sentence_transformers")  # pip: sentence-transformers

    metrics = run_persona_gpt2_eval(quiet=True)
    assert metrics["total_tokens_scored"] > 0
    assert 0.0 < metrics["mean_token_ce"] < 30.0
    assert 1.0 < metrics["perplexity"] < 1e12
    assert 0.0 <= metrics["token_accuracy"] <= 1.0

    if metrics.get("checkpoint"):
        # Trained checkpoint should beat random guessing by a wide margin.
        assert metrics["token_accuracy"] > 0.001
        assert metrics["mean_token_ce"] < 12.0


def test_persona_dataset_parses_examples() -> None:
    import pytest

    pytest.importorskip("torch")
    pytest.importorskip("transformers")
    pytest.importorskip("datasets")

    tpg = _import_train_script()
    hf_cache = tpg.setup_hf_cache()
    tok = tpg.GPT2Tokenizer.from_pretrained(
        "gpt2", cache_dir=str(hf_cache / "transformers")
    )
    tok.pad_token = tok.eos_token
    ds = tpg.PersonaChatDataset(
        "train",
        tok,
        max_length=128,
        max_examples=4,
        datasets_cache=hf_cache / "datasets",
    )
    assert len(ds) > 0
    ex = ds[0]
    assert "persona" in ex and "input_ids" in ex and "target_id" in ex


if __name__ == "__main__":
    try:
        run_persona_gpt2_eval()
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
