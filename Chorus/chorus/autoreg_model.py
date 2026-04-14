"""
Autoregressive token predictor: 384D MiniLM embedding → stacked blocks → logits (CE).

Each block: pairwise P ∈ R^{384×384} → **Gaussian-weighted 8×8 patch sum** → P' ∈ R^{48×48} →
pair_w1…b2 (48×48) → row softmax + rational sum → **48** scalars → **Linear(48,384)** → scale rows
of H → shared MLP. After blocks, **learned softmax over 384 rows** of H → **Linear(64, V)**.

Default V=16000, 5 blocks — softmax-weighted sum over 384 rows → Linear(64, V). Run
`AutoregPredictor.parameter_count_breakdown()`.
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F


def _gaussian_kernel_8x8() -> torch.Tensor:
    """Fixed 8×8 Gaussian (σ relative to center), normalized to sum to 1."""
    t = torch.arange(8, dtype=torch.float32) - 3.5
    g1 = torch.exp(-(t[:, None] ** 2 + t[None, :] ** 2) / (2 * 2.0**2))
    return g1 / g1.sum()


class AutoregBlock(nn.Module):
    """384×384 Gram → 48×48 pooled → 48×48 pair layers → 48→384 scale rows of H → MLP."""

    def __init__(
        self,
        dim: int = 384,
        hidden: int = 64,
        pool_kernel: int = 8,
        eps: float = 1e-6,
        learnable_gaussian: bool = True,
    ) -> None:
        super().__init__()
        if dim % pool_kernel != 0:
            raise ValueError(f"dim {dim} must be divisible by pool_kernel {pool_kernel}")
        self.dim = dim
        self.hidden = hidden
        self.eps = eps
        self.pool_kernel = pool_kernel
        self.grid = dim // pool_kernel

        self.W = nn.Parameter(torch.randn(dim, hidden) * 0.02)
        self.b = nn.Parameter(torch.randn(dim, hidden) * 0.02)

        g = _gaussian_kernel_8x8()
        if learnable_gaussian:
            self.patch_weight = nn.Parameter(g.clone())
        else:
            self.register_buffer("patch_weight", g, persistent=False)

        gsz = self.grid
        self.pair_w1 = nn.Parameter(torch.randn(gsz, gsz) * 0.02)
        self.pair_b1 = nn.Parameter(torch.randn(gsz, gsz) * 0.02)
        self.pair_w2 = nn.Parameter(torch.randn(gsz, gsz) * 0.02)
        self.pair_b2 = nn.Parameter(torch.randn(gsz, gsz) * 0.02)

        self.proj_48_to_384 = nn.Linear(gsz, dim, bias=True)
        self.mlp_down = nn.Linear(hidden, 128)
        self.mlp_up = nn.Linear(128, hidden)
        self.leaky = nn.LeakyReLU(0.01)

    def _patch_gaussian_downsample(self, P: torch.Tensor) -> torch.Tensor:
        """P: (B, D, D) with D=384 → (B, 48, 48) weighted sum over each 8×8 non-overlapping patch."""
        B, D1, D2 = P.shape
        k = self.pool_kernel
        g = self.grid
        assert D1 == D2 == self.dim == g * k
        w = self.patch_weight
        if w.dim() == 2:
            w = F.softmax(w.reshape(-1), dim=0).reshape(k, k)
        # (B,48,8,48,8) row-major blocks → (B,48,48,8,8) patch (i,j,a,b) = P[i*8+a, j*8+b]
        x = P.view(B, g, k, g, k).permute(0, 1, 3, 2, 4).contiguous()
        w_b = w.view(1, 1, 1, k, k)
        return (x * w_b).sum(dim=(-2, -1))

    def _scalars_from_input(self, x: torch.Tensor) -> torch.Tensor:
        if x.dim() == 2 and x.size(-1) == self.dim:
            return x
        if x.dim() == 3 and x.size(1) == self.dim and x.size(2) == self.hidden:
            return x.mean(dim=-1)
        raise ValueError(f"expected (B,{self.dim}) or (B,{self.dim},{self.hidden}), got {tuple(x.shape)}")

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        E = self._scalars_from_input(x)
        s = E.unsqueeze(-1)
        inv = 1.0 / (s.abs() + self.eps)
        H = s * self.W.unsqueeze(0) + inv * self.b.unsqueeze(0)
        H = F.gelu(H)
        P = torch.einsum("bik,bjk->bij", H, H)
        P = self._patch_gaussian_downsample(P)
        P = self.leaky(P * self.pair_w1.unsqueeze(0) + self.pair_b1.unsqueeze(0))
        P = self.leaky(P * self.pair_w2.unsqueeze(0) + self.pair_b2.unsqueeze(0))
        row_soft = F.softmax(P, dim=-1)
        row_weights = (1.0 / (1.0 + row_soft**2)).sum(dim=-1)
        scales = self.proj_48_to_384(row_weights)
        H = H * scales.unsqueeze(-1)
        z = self.mlp_down(H)
        z = self.leaky(z)
        z = self.mlp_up(z)
        z = self.leaky(z)
        return z


class AutoregPredictor(nn.Module):
    """Five AutoregBlocks → row softmax pool → Linear(hidden, V).

    ``dim`` is the token axis size (default 384). If ``encoder_dim`` is set and differs from
    ``dim`` (e.g. encoder outputs 384-D, ``dim=64``), a learned ``Linear(encoder_dim, dim)``
    is applied before the blocks.
    """

    def __init__(
        self,
        dim: int = 384,
        hidden: int = 64,
        num_blocks: int = 5,
        vocab_size: int = 16000,
        proj_dim: int | None = None,
        encoder_dim: int | None = None,
        pool_kernel: int = 8,
        learnable_gaussian: bool = True,
        eps: float = 1e-6,
    ) -> None:
        super().__init__()
        self.dim = dim
        self.hidden = hidden
        self.vocab_size = vocab_size
        # Deprecated: old checkpoints used 64→proj_dim→V; ignored.
        self.proj_dim = proj_dim
        self.encoder_dim = encoder_dim
        if encoder_dim is not None and int(encoder_dim) != int(dim):
            self.encoder_proj = nn.Linear(int(encoder_dim), int(dim))
        else:
            self.encoder_proj = None
        self.blocks = nn.ModuleList(
            [
                AutoregBlock(
                    dim=dim,
                    hidden=hidden,
                    pool_kernel=pool_kernel,
                    eps=eps,
                    learnable_gaussian=learnable_gaussian,
                )
                for _ in range(num_blocks)
            ]
        )
        self.row_score = nn.Linear(hidden, 1)
        self.lm_head = nn.Linear(hidden, vocab_size)

    def forward(self, embedding: torch.Tensor) -> torch.Tensor:
        x: torch.Tensor = embedding
        if self.encoder_proj is not None:
            x = self.encoder_proj(x)
        for block in self.blocks:
            x = block(x)
        # (B, 384, 64) → softmax weights over rows → (B, 64) → logits (B, V)
        s = self.row_score(x).squeeze(-1)
        w = F.softmax(s, dim=-1)
        pooled = (w.unsqueeze(-1) * x).sum(dim=1)
        return self.lm_head(pooled)

    def num_parameters(self) -> int:
        return sum(p.numel() for p in self.parameters() if p.requires_grad)

    def parameter_count_breakdown(self) -> dict[str, int]:
        n_blocks = sum(p.numel() for p in self.blocks.parameters() if p.requires_grad)
        n_row = self.row_score.weight.numel() + self.row_score.bias.numel()
        n_lm = self.lm_head.weight.numel() + self.lm_head.bias.numel()
        n_enc = 0
        if self.encoder_proj is not None:
            n_enc = self.encoder_proj.weight.numel() + self.encoder_proj.bias.numel()
        n_head = n_row + n_lm + n_enc
        b0 = self.blocks[0]
        g = b0.grid
        pair_4 = 4 * g * g
        gauss = b0.patch_weight.numel() if isinstance(b0.patch_weight, nn.Parameter) else 0
        proj = b0.proj_48_to_384.weight.numel() + b0.proj_48_to_384.bias.numel()
        wb = b0.W.numel() + b0.b.numel()
        mlp = (
            b0.mlp_down.weight.numel()
            + b0.mlp_down.bias.numel()
            + b0.mlp_up.weight.numel()
            + b0.mlp_up.bias.numel()
        )
        per_block = n_blocks // len(self.blocks)
        return {
            "blocks_total": n_blocks,
            "per_block": per_block,
            "per_block_components": {
                "W_and_b": wb,
                "gaussian_patch_8x8": gauss,
                "pair_grids_4x48x48": pair_4,
                "proj_48_to_384": proj,
                "mlp_64_128_64": mlp,
            },
            "encoder_proj": n_enc,
            "row_attn_64_to_1": n_row,
            "lm_head_64_to_vocab": n_lm,
            "classifier_total": n_head,
            "total_trainable": self.num_parameters(),
        }

    @torch.no_grad()
    def predict_token_id(self, embedding: torch.Tensor) -> int:
        self.eval()
        if embedding.dim() == 1:
            embedding = embedding.unsqueeze(0)
        logits = self.forward(embedding)
        return int(logits.argmax(dim=-1).item())


def math_breakdown_text(vocab_size: int = 16000, num_blocks: int = 5) -> str:
    """Human-readable parameter math for default architecture."""
    dim, g, k, h = 384, 48, 8, 64
    wb = 2 * dim * h
    gauss = k * k
    pair = 4 * g * g
    proj = g * dim + dim
    mlp = (h * 128 + 128) + (128 * h + h)
    block = wb + gauss + pair + proj + mlp
    row_attn = h * 1 + 1
    head = row_attn + (h * vocab_size + vocab_size)
    total = num_blocks * block + head
    lines = [
        f"Per block (dim={dim}, grid={g}={dim}//8, hidden={h}):",
        f"  W,b:              2×{dim}×{h}     = {wb:,}",
        f"  Gaussian 8×8:   {gauss} (learnable weights, softmax-normalized in forward)",
        f"  pair_w1…b2:      4×{g}×{g}      = {pair:,}",
        f"  Linear 48→384:  {g}×{dim}+{dim} = {proj:,}",
        f"  MLP 64↔128:     {mlp:,}",
        f"  Subtotal/block:  {block:,}",
        f"{num_blocks} blocks:        {num_blocks * block:,}",
        f"Row softmax scores 64→1: {row_attn:,}",
        f"Head 64→{vocab_size}:     {h * vocab_size + vocab_size:,}",
        f"TOTAL trainable:   {total:,}",
    ]
    return "\n".join(lines)
