"""CLIP(현재 방식) vs SigLIP2(한국어 직접) 검색 A/B 비교.

같은 샘플 사진·같은 한국어 질의로 양쪽 top-5를 뽑아 비교한다.
- CLIP: 한국어→영어 사전변환 후 clip-ViT-B-32 (현재 시스템 재현)
- SigLIP2: 한국어 직접 → google/siglip2-base-patch16-256 (다국어)
"""
import os
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image

sys.path.insert(0, ".")
BASE = Path(".")
THUMBS = BASE / "data" / "thumbs"

N_SAMPLE = 250
QUERIES = ["바닷가", "강아지", "밤에 찍은 도시 야경", "맛있는 음식",
           "단풍", "벚꽃", "눈 내린 겨울 풍경", "아기", "커피와 카페", "산"]

# ---------- 샘플 로드 ----------
c = sqlite3.connect("data/photonest.db")
rows = c.execute(
    "SELECT id FROM media WHERE type='image' AND trashed_at IS NULL "
    "ORDER BY RANDOM() LIMIT ?", (N_SAMPLE,)
).fetchall()
ids, imgs = [], []
for (mid,) in rows:
    p = THUMBS / f"{mid}.jpg"
    if p.exists():
        ids.append(mid)
        imgs.append(Image.open(p).convert("RGB"))
print(f"샘플 {len(ids)}장 로드")


def short(mid):
    return mid.split("__")[-1][:24]


# ---------- CLIP (현재 방식) ----------
print("\n[CLIP] 모델 로드…")
from sentence_transformers import SentenceTransformer
from backend import llm

clip_img_model = SentenceTransformer("clip-ViT-B-32")
t = time.time()
clip_img = clip_img_model.encode(imgs, batch_size=16, convert_to_numpy=True,
                                 normalize_embeddings=True, show_progress_bar=False)
print(f"  이미지 임베딩 {time.time()-t:.1f}s")

clip_results = {}
for q in QUERIES:
    en = llm._ko_to_en(q) or q                       # 현재 시스템의 한→영 변환
    qv = clip_img_model.encode([f"a photo of {en}"], convert_to_numpy=True,
                               normalize_embeddings=True)[0]
    sims = clip_img @ qv
    top = np.argsort(-sims)[:5]
    clip_results[q] = [(ids[i], float(sims[i]), en) for i in top]

# ---------- SigLIP2 (한국어 직접) ----------
print("\n[SigLIP2] 모델 로드(다운로드 ~1.5GB 첫 1회)…")
from transformers import AutoModel, AutoProcessor

MODEL = "google/siglip2-base-patch16-256"
sig_model = AutoModel.from_pretrained(MODEL)
sig_proc = AutoProcessor.from_pretrained(MODEL)
sig_model.eval()

t = time.time()
sig_img = []
with torch.no_grad():
    for i in range(0, len(imgs), 16):
        batch = imgs[i:i + 16]
        inp = sig_proc(images=batch, return_tensors="pt")
        emb = sig_model.get_image_features(**inp)
        emb = emb / emb.norm(dim=-1, keepdim=True)
        sig_img.append(emb.cpu().numpy())
sig_img = np.vstack(sig_img)
print(f"  이미지 임베딩 {time.time()-t:.1f}s")

sig_results = {}
with torch.no_grad():
    for q in QUERIES:
        inp = sig_proc(text=[q], return_tensors="pt", padding="max_length", max_length=64)
        emb = sig_model.get_text_features(**inp)
        emb = emb / emb.norm(dim=-1, keepdim=True)
        qv = emb.cpu().numpy()[0]
        sims = sig_img @ qv
        top = np.argsort(-sims)[:5]
        sig_results[q] = [(ids[i], float(sims[i])) for i in top]

# ---------- 결과 출력 ----------
print("\n" + "=" * 70)
for q in QUERIES:
    print(f"\n■ 질의: '{q}'")
    print(f"  [CLIP→영어 '{clip_results[q][0][2]}'] " +
          " / ".join(f"{short(m)}({s:.2f})" for m, s, _ in clip_results[q]))
    print(f"  [SigLIP2 한국어직접]        " +
          " / ".join(f"{short(m)}({s:.2f})" for m, s in sig_results[q]))

# top-1 사진 id를 파일로 (썸네일 시각 확인용)
out = []
for q in QUERIES:
    out.append(f"{q}\tCLIP:{clip_results[q][0][0]}\tSIG:{sig_results[q][0][0]}")
Path("/tmp/ab_top1.tsv").write_text("\n".join(out))
print("\ntop-1 비교 저장: /tmp/ab_top1.tsv")
