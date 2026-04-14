"""Prefix encoder helpers."""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")

from chorus.prefix_encoder import last_nonpad_hidden


def test_last_nonpad_hidden_selects_last_real_token() -> None:
    h = torch.randn(2, 5, 8)
    mask = torch.tensor([[1, 1, 1, 0, 0], [1, 1, 1, 1, 0]])
    out = last_nonpad_hidden(h, mask)
    assert out.shape == (2, 8)
    assert torch.allclose(out[0], h[0, 2])
    assert torch.allclose(out[1], h[1, 3])
