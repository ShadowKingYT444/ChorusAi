"""AutoregPredictor forward shape and cross-entropy.

Greedy prompt/answer comparison: ``pytest -s tests/test_autoreg_model.py -k reconstruction``
(needs checkpoint, BPE JSON, CSV). Optional: ``DISTLM_TEST_TOKENIZER``, ``DISTLM_TEST_CSV``,
``DISTLM_TEST_RECON_MAX``.
"""

from __future__ import annotations

import csv
import inspect
import os
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")
import torch.nn.functional as F

from chorus.autoreg_model import AutoregBlock, AutoregPredictor

_REPO_ROOT = Path(__file__).resolve().parents[1]
AUTOREG_CHECKPOINT = _REPO_ROOT / "autoreg_checkpoint.pt"
_TOKENIZER_NAMES = ("dolly_bpe_tokenizer.json", "dolly_subword_tokenizer.json")
_CSV_NAMES = ("dolly_prompt_response.csv",)


def _find_file(names: tuple[str, ...]) -> Path | None:
    for base in (_REPO_ROOT, _REPO_ROOT / "out", Path.cwd(), Path.cwd() / "out"):
        for name in names:
            p = (base / name).resolve()
            if p.is_file():
                return p
    return None


def _tokenizer_path() -> Path | None:
    env = os.environ.get("DISTLM_TEST_TOKENIZER", "").strip()
    if env:
        p = Path(env).expanduser().resolve()
        if p.is_file():
            return p
    return _find_file(_TOKENIZER_NAMES)


def _first_prompt_response_row() -> tuple[str, str] | None:
    env = os.environ.get("DISTLM_TEST_CSV", "").strip()
    if env:
        path = Path(env).expanduser().resolve()
        if path.is_file():
            with path.open(newline="", encoding="utf-8") as f:
                r = csv.DictReader(f)
                if not r.fieldnames or "prompt" not in r.fieldnames or "response" not in r.fieldnames:
                    return None
                row = next(r, None)
                if row is None:
                    return None
                return (row.get("prompt") or "", row.get("response") or "")
        return None

    path = _find_file(_CSV_NAMES)
    if path is None:
        return None
    with path.open(newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        if not r.fieldnames or "prompt" not in r.fieldnames or "response" not in r.fieldnames:
            return None
        row = next(r, None)
        if row is None:
            return None
        return (row.get("prompt") or "", row.get("response") or "")


def greedy_generate_response_ids(
    prompt: str,
    tokenizer,
    embedder,
    model: AutoregPredictor,
    *,
    max_tokens: int,
) -> list[int]:
    """
    Match ``scripts/train_autoreg_dolly.build_pairs``: at step k, MiniLM sees
    ``prompt`` if k==0 else ``prompt + tokenizer.decode(ids[:k])`` for response ids only.
    """
    ids: list[int] = []
    for _ in range(max_tokens):
        if not ids:
            text = prompt
        else:
            text = prompt + tokenizer.decode(ids)
        emb = embedder.encode(text, normalize_embeddings=True)
        if hasattr(emb, "tolist"):
            emb = emb.tolist()
        tid = model.predict_token_id(torch.tensor(emb, dtype=torch.float32))
        ids.append(tid)
    return ids


def _torch_load(path: Path) -> dict:
    kw: dict = {"map_location": "cpu"}
    try:
        if "weights_only" in inspect.signature(torch.load).parameters:
            kw["weights_only"] = False
    except (TypeError, ValueError):
        pass
    return torch.load(path, **kw)


@pytest.fixture
def autoreg_checkpoint_path() -> Path:
    if not AUTOREG_CHECKPOINT.is_file():
        pytest.skip(f"Missing checkpoint (train or copy to repo root): {AUTOREG_CHECKPOINT}")
    return AUTOREG_CHECKPOINT


def test_block_embedding_input_shape() -> None:
    b = AutoregBlock()
    x = torch.randn(2, 384)
    y = b(x)
    assert y.shape == (2, 384, 64)


def test_block_hidden_input_shape() -> None:
    b = AutoregBlock()
    x = torch.randn(3, 384, 64)
    y = b(x)
    assert y.shape == (3, 384, 64)


def test_predictor_logits_shape() -> None:
    m = AutoregPredictor(vocab_size=16000)
    e = torch.randn(4, 384)
    logits = m(e)
    assert logits.shape == (4, 16000)


def test_predictor_row_softmax_weights_sum_to_one() -> None:
    m = AutoregPredictor(vocab_size=16000)
    e = torch.randn(3, 384)
    x = e
    for block in m.blocks:
        x = block(x)
    s = m.row_score(x).squeeze(-1)
    w = F.softmax(s, dim=-1)
    assert w.shape == (3, 384)
    assert torch.allclose(w.sum(dim=-1), torch.ones(3))


def test_predictor_ce_finite() -> None:
    m = AutoregPredictor(vocab_size=100)
    e = torch.randn(8, 384)
    logits = m(e)
    target = torch.randint(0, 100, (8,), dtype=torch.long)
    loss = F.cross_entropy(logits, target)
    assert loss.isfinite()


def test_param_count_formula() -> None:
    m = AutoregPredictor(vocab_size=16000)
    d = m.parameter_count_breakdown()
    assert d["per_block"] == 93_824
    assert d["blocks_total"] == 5 * 93_824
    assert d["encoder_proj"] == 0
    assert d["row_attn_64_to_1"] == 65
    assert d["lm_head_64_to_vocab"] == 64 * 16_000 + 16_000
    assert d["total_trainable"] == d["blocks_total"] + d["classifier_total"]


def test_encoder_proj_64d_trunk() -> None:
    """384-D encoder output projected to dim=64 before blocks."""
    m = AutoregPredictor(vocab_size=100, dim=64, encoder_dim=384)
    e = torch.randn(3, 384)
    logits = m(e)
    assert logits.shape == (3, 100)
    d = m.parameter_count_breakdown()
    assert d["encoder_proj"] == 384 * 64 + 64  # Linear(384 → 64)


def test_autoreg_checkpoint_loads_and_forward(autoreg_checkpoint_path: Path) -> None:
    """Uses ``autoreg_checkpoint.pt`` at repo root (from ``scripts/train_autoreg_dolly.py``)."""
    blob = _torch_load(autoreg_checkpoint_path)
    assert "model_state" in blob and "vocab_size" in blob
    vs = int(blob["vocab_size"])
    cfg = blob.get("config", {})
    dim = int(blob.get("dim", cfg.get("dim", 384)))
    enc_stored = blob.get("encoder_dim", cfg.get("encoder_dim"))
    if enc_stored is None:
        enc_arg = None
    else:
        ei = int(enc_stored)
        enc_arg = ei if ei != dim else None
    m = AutoregPredictor(vocab_size=vs, dim=dim, encoder_dim=enc_arg)
    try:
        m.load_state_dict(blob["model_state"], strict=True)
    except RuntimeError as e:
        pytest.skip(f"Checkpoint from older architecture (retrain with current head): {e}")
    m.eval()
    if "param_count" in blob:
        assert m.num_parameters() == int(blob["param_count"])
    e = torch.randn(2, 384)
    with torch.no_grad():
        logits = m(e)
    assert logits.shape == (2, vs)
    assert logits.isfinite().all()
    tid = m.predict_token_id(e[0])
    assert 0 <= tid < vs


def test_prompt_answer_reconstruction_vs_original(autoreg_checkpoint_path: Path) -> None:
    """
    Greedy decode (MiniLM context + trained head) vs first CSV row.

    Run with ``pytest -s`` to print original prompt/response vs reconstruction in the terminal.
    Max tokens: env ``DISTLM_TEST_RECON_MAX`` (default 64).

    Requires: ``tokenizers``, ``sentence-transformers``, BPE JSON, ``dolly_prompt_response.csv``.
    """
    pytest.importorskip("tokenizers")
    from tokenizers import Tokenizer

    pytest.importorskip("sentence_transformers")
    from sentence_transformers import SentenceTransformer

    tok_path = _tokenizer_path()
    if tok_path is None:
        pytest.skip(
            f"No BPE tokenizer (set DISTLM_TEST_TOKENIZER or add one of {list(_TOKENIZER_NAMES)} "
            "under repo or out/). Generate: python scripts/dolly_bpe_responses.py --out-dir out"
        )
    row = _first_prompt_response_row()
    if row is None:
        pytest.skip(
            f"No CSV (set DISTLM_TEST_CSV or add {list(_CSV_NAMES)} under repo/out)."
        )

    prompt, original = row
    if not original.strip():
        pytest.skip("First CSV row has empty response")

    blob = _torch_load(autoreg_checkpoint_path)
    vs = int(blob["vocab_size"])
    minilm = str(blob.get("minilm_model", "sentence-transformers/all-MiniLM-L6-v2"))

    model = AutoregPredictor(vocab_size=vs)
    try:
        model.load_state_dict(blob["model_state"], strict=True)
    except RuntimeError as e:
        pytest.skip(f"Checkpoint incompatible with current architecture (retrain): {e}")
    model.eval()

    tok = Tokenizer.from_file(str(tok_path))
    embedder = SentenceTransformer(minilm)

    enc = tok.encode(original)
    target_len = len(enc.ids)
    max_tokens = int(os.environ.get("DISTLM_TEST_RECON_MAX", "64"))
    max_tokens = min(max_tokens, target_len + 32)

    gen_ids = greedy_generate_response_ids(
        prompt,
        tok,
        embedder,
        model,
        max_tokens=max_tokens,
    )
    reconstructed = tok.decode(gen_ids)

    orig_ids = enc.ids
    n = min(len(orig_ids), len(gen_ids))
    token_matches = sum(1 for i in range(n) if orig_ids[i] == gen_ids[i]) if n else 0
    prefix_rate = token_matches / max(len(orig_ids), 1)

    msg = "\n".join(
        [
            "",
            "========== PROMPT / ANSWER RECONSTRUCTION ==========",
            "--- original prompt ---",
            prompt[:4000] + ("…" if len(prompt) > 4000 else ""),
            "",
            "--- original response (first CSV row; teacher-forcing target) ---",
            original[:4000] + ("…" if len(original) > 4000 else ""),
            "",
            f"--- greedy reconstruction ({len(gen_ids)} tokens, max_tokens={max_tokens}) ---",
            reconstructed[:4000] + ("…" if len(reconstructed) > 4000 else ""),
            "",
            "--- token prefix match vs original --- "
            f"{token_matches}/{len(orig_ids)} tokens ({prefix_rate:.1%} of original length)",
            "====================================================",
            "",
        ]
    )
    print(msg, flush=True)

    assert len(gen_ids) > 0
    assert model.vocab_size == vs
    assert isinstance(reconstructed, str)
