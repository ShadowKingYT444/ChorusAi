#!/usr/bin/env python3
"""
Enter a prompt, get an autoregressive response (MiniLM + trained AutoregPredictor).

  python prompt.py
  python prompt.py "What is 2+2?"

Defaults: ``autoreg_checkpoint.pt`` and BPE tokenizer under repo root or ``out/``.
Override: ``CHORUS_ROOT``, ``CHORUS_DATA_DIR``, ``CHORUS_TOKENIZER`` (path to BPE JSON, same as training),
or ``--checkpoint`` / ``--tokenizer``.

Requires: torch, sentence-transformers, tokenizers (same as training).

Greedy decoding can collapse to one token (e.g. commas). Defaults apply a repetition penalty and
stop after many identical tokens; use ``--temperature 0.85`` if output is still degenerate.
"""

from __future__ import annotations

import argparse
import inspect
import os
import sys
from pathlib import Path


def _root() -> Path:
    env = os.environ.get("CHORUS_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parent


ROOT = _root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _data_dir() -> Path:
    raw = os.environ.get("CHORUS_DATA_DIR", "out").strip() or "out"
    p = Path(raw).expanduser()
    return p.resolve() if p.is_absolute() else (ROOT / p).resolve()


_DATA_DIR = _data_dir()
_TOKENIZER_NAMES = ("dolly_bpe_tokenizer.json", "dolly_subword_tokenizer.json")
_CKPT_NAMES = ("autoreg_checkpoint.pt",)


def _search_file(names: tuple[str, ...], bases: list[Path]) -> Path | None:
    seen: set[str] = set()
    for base in bases:
        try:
            b = base.resolve()
        except OSError:
            continue
        key = str(b)
        if key in seen:
            continue
        seen.add(key)
        for name in names:
            cand = (b / name).resolve()
            if cand.is_file():
                return cand
    return None


def _default_tokenizer() -> Path | None:
    env = os.environ.get("CHORUS_TOKENIZER", "").strip()
    if env:
        p = Path(env).expanduser().resolve()
        if p.is_file():
            return p
    found = _search_file(
        _TOKENIZER_NAMES,
        [
            ROOT,
            Path.cwd(),
            ROOT / "out",
            Path.cwd() / "out",
            _DATA_DIR,
            ROOT / "dolly_bpe_responses",
        ],
    )
    if found is not None:
        return found
    # Repo-wide fallback (e.g. tokenizer only in a subfolder)
    root_res = ROOT.resolve()
    for name in _TOKENIZER_NAMES:
        for p in ROOT.rglob(name):
            if not p.is_file():
                continue
            try:
                p.resolve().relative_to(root_res)
            except ValueError:
                continue
            return p.resolve()
    return None


def _default_checkpoint() -> Path | None:
    return _search_file(
        _CKPT_NAMES,
        [ROOT, Path.cwd(), ROOT / "out", Path.cwd() / "out", _DATA_DIR],
    )


def _torch_load(path: Path) -> dict:
    import torch

    kw: dict = {"map_location": "cpu"}
    try:
        if "weights_only" in inspect.signature(torch.load).parameters:
            kw["weights_only"] = False
    except (TypeError, ValueError):
        pass
    return torch.load(path, **kw)


def _apply_repetition_penalty(
    logits: torch.Tensor, seen_ids: list[int], penalty: float
) -> torch.Tensor:
    """HF-style: down-weight logits for tokens already generated (reduces comma loops)."""
    if penalty == 1.0 or not seen_ids:
        return logits
    out = logits.clone()
    for tid in set(seen_ids):
        if out[tid] > 0:
            out[tid] /= penalty
        else:
            out[tid] *= penalty
    return out


def _pick_token_id(
    logits_1d: torch.Tensor,
    *,
    seen_ids: list[int],
    repetition_penalty: float,
    temperature: float,
) -> int:
    import torch
    import torch.nn.functional as F

    x = _apply_repetition_penalty(logits_1d, seen_ids, repetition_penalty)
    if temperature > 0:
        probs = F.softmax(x / temperature, dim=-1)
        return int(torch.multinomial(probs, 1).item())
    return int(x.argmax(dim=-1).item())


def generate(
    prompt: str,
    *,
    checkpoint: Path,
    tokenizer_path: Path,
    max_tokens: int,
    minilm_model: str | None,
    repetition_penalty: float = 1.2,
    temperature: float = 0.0,
    max_same_token_run: int = 8,
    seed: int | None = None,
    embed_mode: str | None = None,
) -> str:
    import torch
    from tokenizers import Tokenizer

    from chorus.autoreg_model import AutoregPredictor
    from chorus.prefix_encoder import make_prefix_encoder

    ck = _torch_load(checkpoint)
    cfg = ck.get("config", {})
    vs = int(ck.get("vocab_size", cfg.get("vocab_size", 16000)))
    minilm = minilm_model or ck.get("minilm_model", "sentence-transformers/all-MiniLM-L6-v2")
    emode = embed_mode or ck.get("embed_mode") or cfg.get("embed_mode", "sentence")
    dim = int(ck.get("dim", cfg.get("dim", 384)))
    enc_stored = ck.get("encoder_dim", cfg.get("encoder_dim"))
    if enc_stored is None:
        enc_arg = None
    else:
        ei = int(enc_stored)
        enc_arg = ei if ei != dim else None

    model = AutoregPredictor(vocab_size=vs, dim=dim, encoder_dim=enc_arg)
    try:
        model.load_state_dict(ck["model_state"], strict=True)
    except RuntimeError:
        model.load_state_dict(ck["model_state"], strict=False)
    model.eval()

    tok = Tokenizer.from_file(str(tokenizer_path))

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)
    prefix_enc = make_prefix_encoder(emode, minilm, device)

    if seed is not None:
        torch.manual_seed(seed)

    text = prompt
    out_ids: list[int] = []
    with torch.no_grad():
        for _ in range(max_tokens):
            t = prefix_enc.encode(text)
            logits = model(t).squeeze(0)
            tid = _pick_token_id(
                logits,
                seen_ids=out_ids,
                repetition_penalty=repetition_penalty,
                temperature=temperature,
            )
            out_ids.append(tid)
            text = text + tok.decode([tid])
            if (
                max_same_token_run >= 2
                and len(out_ids) >= max_same_token_run
                and len(set(out_ids[-max_same_token_run:])) == 1
            ):
                break

    return tok.decode(out_ids)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "prompt",
        nargs="*",
        help="Prompt text (if omitted, reads one line from stdin)",
    )
    p.add_argument(
        "--checkpoint",
        type=Path,
        default=None,
        help=f"Default: search for {list(_CKPT_NAMES)} under repo/out",
    )
    p.add_argument(
        "--tokenizer",
        type=Path,
        default=None,
        help=f"Default: search for {list(_TOKENIZER_NAMES)} under repo/out",
    )
    p.add_argument("--max-tokens", type=int, default=128)
    p.add_argument("--minilm-model", default=None)
    p.add_argument(
        "--embed-mode",
        choices=("sentence", "token_last"),
        default=None,
        help="Override checkpoint: sentence vs last HF token hidden (default: from checkpoint)",
    )
    p.add_argument(
        "--repetition-penalty",
        type=float,
        default=1.2,
        help=">1 down-weights logits for tokens already emitted (default 1.2; set 1 to disable)",
    )
    p.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="0 = greedy argmax; try 0.8–1.0 + --seed to sample if output repeats one token",
    )
    p.add_argument(
        "--max-same-token-run",
        type=int,
        default=8,
        help="Stop if the same token id repeats this many times in a row (0 = no limit)",
    )
    p.add_argument("--seed", type=int, default=None, help="RNG seed when temperature > 0")
    args = p.parse_args()

    ck_path = args.checkpoint
    if ck_path is None:
        found = _default_checkpoint()
        if found is None:
            print(
                f"Error: no checkpoint. Put {_CKPT_NAMES[0]} in repo root or ./out, "
                "or pass --checkpoint",
                file=sys.stderr,
            )
            raise SystemExit(1)
        ck_path = found
        print(f"Using checkpoint: {ck_path}", flush=True)
    else:
        ck_path = ck_path.expanduser().resolve()

    tok_path = args.tokenizer
    if tok_path is None:
        found = _default_tokenizer()
        if found is None:
            print(
                "Error: no BPE tokenizer JSON found.\n"
                f"  • export CHORUS_TOKENIZER=/path/to/{_TOKENIZER_NAMES[0]}\n"
                "  • or: python scripts/dolly_bpe_responses.py --out-dir out\n"
                "  • or: python prompt.py --tokenizer /path/to/dolly_bpe_tokenizer.json",
                file=sys.stderr,
            )
            raise SystemExit(1)
        tok_path = found
        print(f"Using tokenizer: {tok_path}", flush=True)
    else:
        tok_path = tok_path.expanduser().resolve()

    if not ck_path.is_file():
        raise SystemExit(f"Not found: {ck_path}")
    if not tok_path.is_file():
        raise SystemExit(f"Not found: {tok_path}")

    if args.prompt:
        user_prompt = " ".join(args.prompt).strip()
    else:
        print("Prompt: ", end="", flush=True)
        user_prompt = sys.stdin.readline().strip()

    if not user_prompt:
        raise SystemExit("Empty prompt.")

    response = generate(
        user_prompt,
        checkpoint=ck_path,
        tokenizer_path=tok_path,
        max_tokens=args.max_tokens,
        minilm_model=args.minilm_model,
        repetition_penalty=args.repetition_penalty,
        temperature=args.temperature,
        max_same_token_run=args.max_same_token_run,
        seed=args.seed,
        embed_mode=args.embed_mode,
    )
    print(response)


if __name__ == "__main__":
    main()
