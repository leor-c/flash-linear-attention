# -*- coding: utf-8 -*-

import os

import pytest
import torch
import torch.nn.functional as F

from fla.ops.ttt import chunk_ttt_linear
from fla.ops.ttt import fused_chunk_ttt_linear
from fla.ops.ttt.naive import chunk_ttt_linear_ref


def get_abs_err(x, y):
    return (x-y).flatten().abs().max().item()


def get_err_ratio(x, y):
    err = (x-y).flatten().square().mean().sqrt().item()
    base = (x).flatten().square().mean().sqrt().item()
    return err / base


def assert_close(prefix, ref, tri, ratio):
    msg = f"{prefix} diff: {get_abs_err(ref, tri):.6f} ratio: {get_err_ratio(ref, tri):.6f}"
    print(msg)
    assert get_err_ratio(ref, tri) < ratio, msg


@pytest.mark.parametrize("B", [2])
@pytest.mark.parametrize("T", [16, 30, 32, 63, 64, 256])
@pytest.mark.parametrize("H", [2, 16])
@pytest.mark.parametrize("D", [30, 64, 100])
@pytest.mark.parametrize("scale", [0.1])
@pytest.mark.parametrize("dtype", [torch.bfloat16])
@pytest.mark.parametrize("head_first", [True, False])
def test_chunk(
    B: int,
    T: int,
    H: int,
    D: int,
    dtype: torch.dtype,
    scale: float,
    head_first: bool
):
    eta_base = 5e-3
    if head_first:
        q = torch.randn(B, H, T, D, dtype=dtype)
        k = F.normalize(torch.randn(B, H, T, D, dtype=torch.float32), p=2, dim=-1).to(dtype)
        v = torch.randn(B, H, T, D, dtype=dtype)
        w = torch.randn(H, D, dtype=dtype)
        b = torch.randn(H, D, dtype=dtype)
        eta = torch.randn(B, H, T, 1, dtype=dtype) * eta_base
        h0 = torch.randn(B, H, D, D, dtype=torch.float32)
        hb0 = torch.randn(B, H, 1, D, dtype=torch.float32)
    else:
        q = torch.randn(B, T, H, D, dtype=dtype)
        k = F.normalize(torch.randn(B, T, H, D, dtype=torch.float32), p=2, dim=-1).to(dtype)
        v = torch.randn(B, T, H, D, dtype=dtype)
        w = torch.randn(H, D, dtype=dtype)
        b = torch.randn(H, D, dtype=dtype)
        eta = torch.randn(B, T, H, 1, dtype=dtype) * eta_base
        h0 = torch.randn(B, H, D, D, dtype=torch.float32)
        hb0 = torch.randn(B, H, 1, D, dtype=torch.float32)

    q, k, v, w, b, eta, h0, hb0 = map(lambda x: x.cuda().requires_grad_(True), (q, k, v, w, b, eta, h0, hb0))
    do = torch.rand_like(v)
    dht = torch.rand_like(h0)
    dhbt = torch.rand_like(hb0)

    tri, tri_ht, tri_hbt = chunk_ttt_linear(
        q.clone(),
        k.clone(),
        v.clone(),
        w.clone(),
        b.clone(),
        eta.clone(),
        scale=scale,
        output_final_state=True,
        initial_state=h0.clone(),
        initial_state_bias=hb0.clone(),
        head_first=head_first
    )
    ((tri * do).sum() + (tri_ht * dht).sum() +  (tri_hbt * dhbt).sum()).backward(retain_graph=True)
    tri_dq, tri_dk, tri_dv, tri_dw, tri_db, tri_deta, tri_dh0, tri_dhb0 = q.grad, k.grad, v.grad, w.grad, b.grad, eta.grad, h0.grad, hb0.grad
    q.grad = k.grad = v.grad = w.grad = b.grad = eta.grad = h0.grad = hb0.grad = None

    ref, ref_ht, ref_hbt = chunk_ttt_linear_ref(
        q.clone(),
        k.clone(),
        v.clone(),
        w.clone(),
        b.clone(),
        eta.clone(),
        scale=scale,
        output_final_state=True,
        initial_state=h0.clone(),
        initial_state_bias=hb0.clone(),
        head_first=head_first
    )
    ((ref * do).sum() + (ref_ht * dht).sum() +  (ref_hbt * dhbt).sum()).backward(retain_graph=True)
    ref_dq, ref_dk, ref_dv, ref_dw, ref_db, ref_deta, ref_dh0, ref_dhb0 =  q.grad, k.grad, v.grad, w.grad, b.grad, eta.grad, h0.grad, hb0.grad
    
    assert_close("   o", ref, tri, 0.005)
    assert_close("  ht", ref_ht, tri_ht, 0.005)
    assert_close(" hbt", ref_hbt, tri_hbt, 0.005)
    assert_close("  dq", ref_dq, tri_dq, 0.005)
    assert_close("  dk", ref_dk, tri_dk, 0.010)
    assert_close("  dv", ref_dv, tri_dv, 0.007)
    assert_close("  dw", ref_dw, tri_dw, 0.006)
    assert_close("  db", ref_db, tri_db, 0.006)
    assert_close("  de", ref_deta, tri_deta, 0.030)  # because the last element of the chunk
    if head_first:
        assert_close(" de0", ref_deta[:,:,:14,:], tri_deta[:,:,:14,:], 0.010)
    else:
        assert_close(" de0", ref_deta[:,:14,:,:], tri_deta[:,:14,:,:], 0.010)
    assert_close(" dh0", ref_dh0, tri_dh0, 0.007)
    assert_close("dhb0", ref_dhb0, tri_dhb0, 0.005)


@pytest.mark.parametrize("B", [2])
@pytest.mark.parametrize("T", [16, 30, 32, 63, 64, 256])
@pytest.mark.parametrize("H", [2, 16])
@pytest.mark.parametrize("D", [30, 64, 100])
@pytest.mark.parametrize("scale", [0.1])
@pytest.mark.parametrize("dtype", [torch.bfloat16])
@pytest.mark.parametrize("head_first", [True, False])
def test_fused_chunk_fwd(
    B: int,
    T: int,
    H: int,
    D: int,
    dtype: torch.dtype,
    scale: float,
    head_first: bool
):
    eta_base = 5e-3
    if head_first:
        q = torch.randn(B, H, T, D, dtype=dtype)
        k = F.normalize(torch.randn(B, H, T, D, dtype=torch.float32), p=2, dim=-1).to(dtype)
        v = torch.randn(B, H, T, D, dtype=dtype)
        w = torch.randn(H, D, dtype=dtype)
        b = torch.randn(H, D, dtype=dtype)
        eta = torch.randn(B, H, T, 1, dtype=dtype) * eta_base
        h0 = torch.randn(B, H, D, D, dtype=torch.float32)
        hb0 = torch.randn(B, H, 1, D, dtype=torch.float32)
    else:
        q = torch.randn(B, T, H, D, dtype=dtype)
        k = F.normalize(torch.randn(B, T, H, D, dtype=torch.float32), p=2, dim=-1).to(dtype)
        v = torch.randn(B, T, H, D, dtype=dtype)
        w = torch.randn(H, D, dtype=dtype)
        b = torch.randn(H, D, dtype=dtype)
        eta = torch.randn(B, T, H, 1, dtype=dtype) * eta_base
        h0 = torch.randn(B, H, D, D, dtype=torch.float32)
        hb0 = torch.randn(B, H, 1, D, dtype=torch.float32)

    q, k, v, w, b, eta, h0, hb0 = map(lambda x: x.cuda().requires_grad_(True), (q, k, v, w, b, eta, h0, hb0))
    do = torch.rand_like(v)
    dht = torch.rand_like(h0)
    dhbt = torch.rand_like(hb0)

    tri, tri_ht, tri_hbt = fused_chunk_ttt_linear(
        q.clone(),
        k.clone(),
        v.clone(),
        w.clone(),
        b.clone(),
        eta.clone(),
        scale=scale,
        output_final_state=True,
        initial_state=h0.clone(),
        initial_state_bias=hb0.clone(),
        head_first=head_first
    )
    ((tri * do).sum() + (tri_ht * dht).sum() +  (tri_hbt * dhbt).sum()).backward(retain_graph=True)
    tri_dq, tri_dk, tri_dv, tri_dw, tri_db, tri_deta, tri_dh0, tri_dhb0 = q.grad, k.grad, v.grad, w.grad, b.grad, eta.grad, h0.grad, hb0.grad
    q.grad = k.grad = v.grad = w.grad = b.grad = eta.grad = h0.grad = hb0.grad = None

    ref, ref_ht, ref_hbt = chunk_ttt_linear_ref(
        q.clone(),
        k.clone(),
        v.clone(),
        w.clone(),
        b.clone(),
        eta.clone(),
        scale=scale,
        output_final_state=True,
        initial_state=h0.clone(),
        initial_state_bias=hb0.clone(),
        head_first=head_first
    )
    ((ref * do).sum() + (ref_ht * dht).sum() +  (ref_hbt * dhbt).sum()).backward(retain_graph=True)
    ref_dq, ref_dk, ref_dv, ref_dw, ref_db, ref_deta, ref_dh0, ref_dhb0 =  q.grad, k.grad, v.grad, w.grad, b.grad, eta.grad, h0.grad, hb0.grad
    
    assert_close("   o", ref, tri, 0.005)
    assert_close("  ht", ref_ht, tri_ht, 0.005)
    assert_close(" hbt", ref_hbt, tri_hbt, 0.005)
    assert_close("  dq", ref_dq, tri_dq, 0.005)
    assert_close("  dk", ref_dk, tri_dk, 0.010)
    assert_close("  dv", ref_dv, tri_dv, 0.007)
    assert_close("  dw", ref_dw, tri_dw, 0.005)
    assert_close("  db", ref_db, tri_db, 0.005)
    assert_close("  de", ref_deta, tri_deta, 0.03)  # because the last element of the chunk
    if head_first:
        assert_close(" de0", ref_deta[:,:,:14,:], tri_deta[:,:,:14,:], 0.008)
    else:
        assert_close(" de0", ref_deta[:,:14,:,:], tri_deta[:,:14,:,:], 0.008)
    assert_close(" dh0", ref_dh0, tri_dh0, 0.006)
    assert_close("dhb0", ref_dhb0, tri_dhb0, 0.005)


@pytest.mark.parametrize("N", [4])
@pytest.mark.parametrize("T", [64, 128, 200, 250, 256, 300, 400, 500])
@pytest.mark.parametrize("H", [2, 16])
@pytest.mark.parametrize("D", [50, 63, 64, 100])
@pytest.mark.parametrize("scale", [0.1])
@pytest.mark.parametrize("dtype", [torch.bfloat16])
def test_chunk_varlen_fwd(
    N: int,
    T: int,
    H: int,
    D: int,
    scale: float,
    dtype: torch.dtype,
):
    torch.manual_seed(42)
    os.environ['TRITON_F32_DEFAULT'] = 'ieee'
    # randomly split the sequence into N segments
    offsets = torch.cat([
        torch.tensor([0], dtype=torch.long),
        torch.arange(16, T)[torch.randperm(T - 1)[:N-1]],
        torch.tensor([T], dtype=torch.long)
    ], 0).cuda().sort()[0]
    eta_base = 5e-3
    # seq-first required for inputs with variable lengths
    q = torch.randn((1, T, H, D), dtype=dtype)
    k = F.normalize(torch.randn(1, T, H, D, dtype=torch.float32), p=2, dim=-1).to(dtype)
    v = torch.randn((1, T, H, D), dtype=dtype)
    eta = torch.randn(1, T, H, 1, dtype=dtype) * eta_base
    w = torch.randn(H, D, dtype=dtype)
    b = torch.randn(H, D, dtype=dtype)
    h0 = torch.randn((N, H, D, D), dtype=torch.float32)
    hb0 = torch.randn((N, H, 1, D), dtype=torch.float32)
    q, k, v, w, b, eta, h0, hb0 = map(lambda x: x.cuda().requires_grad_(), (q, k, v, w, b, eta, h0, hb0))

    tri, tri_ht, tri_hbt = chunk_ttt_linear(
        q.clone(),
        k.clone(),
        v.clone(),
        w.clone(),
        b.clone(),
        eta.clone(),
        scale=scale,
        output_final_state=True,
        initial_state=h0.clone(),
        initial_state_bias=hb0.clone(),
        cu_seqlens=offsets,
        head_first=False
    )

    ref = []
    ref_ht = []
    ref_hbt = []
    for i in range(N):
        ref_i, ref_ht_i, ref_hbt_i = chunk_ttt_linear_ref(
            q=q[:, offsets[i]:offsets[i+1]],
            k=k[:, offsets[i]:offsets[i+1]],
            v=v[:, offsets[i]:offsets[i+1]],
            w=w,
            b=b,
            eta=eta[:, offsets[i]:offsets[i+1]],
            scale=scale,
            initial_state=h0[i],
            initial_state_bias=hb0[i],
            output_final_state=True,
            head_first=False
        )
        ref.append(ref_i)
        ref_ht.append(ref_ht_i)
        ref_hbt.append(ref_hbt_i)
    ref = torch.cat(ref, 1)
    ref_ht = torch.cat(ref_ht, 0)
    ref_hbt = torch.cat(ref_hbt, 0)

    assert_close("  o", ref, tri, 0.005)
    assert_close(" ht", ref_ht, tri_ht, 0.005)
    assert_close("hbt", ref_hbt, tri_hbt, 0.005)
