"""
Train the India Physics Engine using 1.58-bit ternary BitLinear layers.

Input:
    /kaggle/working/indian_physics_training.csv

Outputs:
    /kaggle/working/model_artifacts/physics_bitmlp/
        physics_bitmlp_realvalued_train_state.pt
        physics_bitmlp_ternary_edge.pt
        physics_bitmlp_torchscript.pt
        physics_feature_metadata.json

Kaggle:
    !python /kaggle/working/train_indian_physics_bitmlp.py

Fast test:
    %env PHYSICS_EPOCHS=1
    %env PHYSICS_MAX_ROWS=5000
    !python /kaggle/working/train_indian_physics_bitmlp.py
"""

from __future__ import annotations

import json
import math
import os
import random
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, TensorDataset


SEED = int(os.getenv("SEED", "2026"))
WORK_DIR = Path(os.getenv("WORK_DIR", "/kaggle/working" if Path("/kaggle/working").exists() else "."))
CSV_PATH = Path(os.getenv("PHYSICS_CSV", str(WORK_DIR / "indian_physics_training.csv")))
OUT_DIR = Path(os.getenv("PHYSICS_OUT_DIR", str(WORK_DIR / "model_artifacts" / "physics_bitmlp")))

MAX_ROWS = int(os.getenv("PHYSICS_MAX_ROWS", "0"))  # 0 means all rows.
EPOCHS = int(os.getenv("PHYSICS_EPOCHS", "14"))
BATCH_SIZE = int(os.getenv("PHYSICS_BATCH_SIZE", "2048"))
LR = float(os.getenv("PHYSICS_LR", "2e-3"))
HIDDEN_DIM = int(os.getenv("PHYSICS_HIDDEN_DIM", "256"))
DEPTH = int(os.getenv("PHYSICS_DEPTH", "4"))
DROPOUT = float(os.getenv("PHYSICS_DROPOUT", "0.05"))


NUMERIC_FEATURES = [
    "room_width_ft",
    "room_length_ft",
    "column_width_mm",
    "floors",
    "ceiling_height_ft",
    "has_beam",
    "ductile_detailing",
    "tier_multiplier",
    "governing_span_ft",
    "area_sqft",
    "aspect_ratio",
    "effective_span_limit_ft",
    "seismic_zone_factor",
    "required_column_width_mm",
    "epoxy_tmt_required",
    "damp_proofing_required",
    "thermal_mass_required",
    "snow_roof_required",
    "engine_override_active",
]

CATEGORICAL_FEATURES = [
    "material_type",
    "wall_material",
    "roofing_type",
    "foundation_type",
    "steel_grade",
    "soil_type",
    "city",
    "state",
    "cost_tier",
    "seismic_zone",
    "climate",
    "required_steel_grade",
    "required_foundation_type",
    "required_roofing_type",
]

TARGET_SAFE = "is_structurally_safe"
TARGET_REGRESSION = ["cost_inr", "carbon_kg"]


def set_seed(seed: int) -> None:
    random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


class TernaryWeightSTE(torch.autograd.Function):
    """Straight-through estimator for {-1, 0, +1} 1.58-bit weights."""

    @staticmethod
    def forward(ctx, weight):
        scale = weight.abs().mean(dim=1, keepdim=True).clamp_min(1e-6)
        ternary = torch.clamp(torch.round(weight / scale), min=-1, max=1)
        return ternary * scale

    @staticmethod
    def backward(ctx, grad_output):
        return grad_output


class BitLinear(nn.Module):
    """Trainable ternary linear layer with full-precision shadow weights."""

    def __init__(self, in_features: int, out_features: int, bias: bool = True):
        super().__init__()
        self.in_features = in_features
        self.out_features = out_features
        self.weight = nn.Parameter(torch.empty(out_features, in_features))
        self.bias = nn.Parameter(torch.zeros(out_features)) if bias else None
        nn.init.xavier_uniform_(self.weight)

    def forward(self, x):
        q_weight = TernaryWeightSTE.apply(self.weight)
        return F.linear(x, q_weight, self.bias)


class PhysicsBitMLP(nn.Module):
    """Multi-task model: safety logit, scaled INR cost, scaled carbon."""

    def __init__(self, input_dim: int, hidden_dim: int, depth: int, dropout: float):
        super().__init__()
        layers: List[nn.Module] = []
        dim = input_dim
        for _ in range(depth):
            layers.extend(
                [
                    BitLinear(dim, hidden_dim),
                    nn.GELU(),
                    nn.LayerNorm(hidden_dim),
                    nn.Dropout(dropout),
                ]
            )
            dim = hidden_dim
        self.backbone = nn.Sequential(*layers)
        self.head = BitLinear(hidden_dim, 3)

    def forward(self, x):
        return self.head(self.backbone(x))


def train_val_split(n: int, val_fraction: float = 0.10) -> Tuple[List[int], List[int]]:
    idx = list(range(n))
    random.shuffle(idx)
    val_n = max(1, int(n * val_fraction))
    return idx[val_n:], idx[:val_n]


def prepare_features(df: pd.DataFrame, train_idx: List[int]) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    features = df[NUMERIC_FEATURES].astype("float32").copy()
    numeric_stats: Dict[str, Dict[str, float]] = {}

    for col in NUMERIC_FEATURES:
        mean = float(features.iloc[train_idx][col].mean())
        std = float(features.iloc[train_idx][col].std())
        if not math.isfinite(std) or std < 1e-8:
            std = 1.0
        features[col] = (features[col] - mean) / std
        numeric_stats[col] = {"mean": mean, "std": std}

    categories: Dict[str, List[str]] = {}
    cat_frames = []
    for col in CATEGORICAL_FEATURES:
        values = sorted(str(v) for v in df[col].fillna("NA").unique())
        categories[col] = values
        cat = pd.Categorical(df[col].fillna("NA").astype(str), categories=values)
        cat_frames.append(pd.get_dummies(cat, prefix=col, dtype="float32"))

    feature_df = pd.concat([features, *cat_frames], axis=1)
    metadata = {
        "numeric_features": NUMERIC_FEATURES,
        "categorical_features": CATEGORICAL_FEATURES,
        "categories": categories,
        "numeric_stats": numeric_stats,
        "feature_columns": list(feature_df.columns),
    }
    return feature_df.astype("float32"), metadata


def make_datasets(df: pd.DataFrame, feature_df: pd.DataFrame, train_idx: List[int], val_idx: List[int]):
    regression = df[TARGET_REGRESSION].astype("float32")
    reg_train = regression.iloc[train_idx]
    target_stats = {
        col: {
            "mean": float(reg_train[col].mean()),
            "std": float(reg_train[col].std() if reg_train[col].std() > 1e-8 else 1.0),
        }
        for col in TARGET_REGRESSION
    }

    reg_scaled = pd.DataFrame()
    for col in TARGET_REGRESSION:
        reg_scaled[col] = (regression[col] - target_stats[col]["mean"]) / target_stats[col]["std"]

    safe = df[TARGET_SAFE].astype("float32")

    def pack(indices: List[int]) -> TensorDataset:
        x = torch.tensor(feature_df.iloc[indices].values, dtype=torch.float32)
        y_safe = torch.tensor(safe.iloc[indices].values, dtype=torch.float32).unsqueeze(1)
        y_reg = torch.tensor(reg_scaled.iloc[indices].values, dtype=torch.float32)
        return TensorDataset(x, y_safe, y_reg)

    return pack(train_idx), pack(val_idx), target_stats


def evaluate(model, loader, device, target_stats: Dict[str, Dict[str, float]]) -> Dict[str, float]:
    model.eval()
    total_loss = 0.0
    total = 0
    correct = 0
    cost_abs = 0.0
    carbon_abs = 0.0

    with torch.no_grad():
        for xb, y_safe, y_reg in loader:
            xb = xb.to(device, non_blocking=True)
            y_safe = y_safe.to(device, non_blocking=True)
            y_reg = y_reg.to(device, non_blocking=True)
            out = model(xb)
            safe_logit, reg_pred = out[:, :1], out[:, 1:]
            loss = F.binary_cross_entropy_with_logits(safe_logit, y_safe) + 0.75 * F.smooth_l1_loss(reg_pred, y_reg)

            pred_safe = (torch.sigmoid(safe_logit) >= 0.5).float()
            correct += int((pred_safe == y_safe).sum().cpu())

            cost_pred = reg_pred[:, 0] * target_stats["cost_inr"]["std"] + target_stats["cost_inr"]["mean"]
            cost_true = y_reg[:, 0] * target_stats["cost_inr"]["std"] + target_stats["cost_inr"]["mean"]
            carbon_pred = reg_pred[:, 1] * target_stats["carbon_kg"]["std"] + target_stats["carbon_kg"]["mean"]
            carbon_true = y_reg[:, 1] * target_stats["carbon_kg"]["std"] + target_stats["carbon_kg"]["mean"]

            cost_abs += float(torch.abs(cost_pred - cost_true).sum().cpu())
            carbon_abs += float(torch.abs(carbon_pred - carbon_true).sum().cpu())
            total_loss += float(loss.cpu()) * xb.shape[0]
            total += xb.shape[0]

    return {
        "loss": total_loss / max(total, 1),
        "safe_acc": correct / max(total, 1),
        "cost_mae_inr": cost_abs / max(total, 1),
        "carbon_mae_kg": carbon_abs / max(total, 1),
    }


def export_ternary(model: nn.Module, metadata: Dict[str, Any]) -> Dict[str, Any]:
    layers: Dict[str, Dict[str, Any]] = {}
    aux: Dict[str, Any] = {}

    for name, module in model.named_modules():
        if isinstance(module, BitLinear):
            weight = module.weight.detach().cpu()
            scale = weight.abs().mean(dim=1, keepdim=True).clamp_min(1e-6)
            codes = torch.clamp(torch.round(weight / scale), min=-1, max=1).to(torch.int8)
            layers[name] = {
                "weight_codes_int8": codes,
                "scale_fp16": scale.squeeze(1).to(torch.float16),
                "bias_fp16": None if module.bias is None else module.bias.detach().cpu().to(torch.float16),
                "in_features": module.in_features,
                "out_features": module.out_features,
            }

    for name, tensor in model.state_dict().items():
        layer_name = name.rsplit(".", 1)[0]
        if layer_name in layers:
            continue
        aux[name] = tensor.detach().cpu().to(torch.float16 if tensor.is_floating_point() else tensor.dtype)

    return {
        "format": "india_physics_bitlinear_1p58_ternary",
        "bitlinear_layers": layers,
        "auxiliary_state": aux,
        "metadata": metadata,
    }


def make_traceable_edge_model(model: PhysicsBitMLP) -> nn.Module:
    class FrozenTernaryLinear(nn.Module):
        def __init__(self, layer: BitLinear):
            super().__init__()
            weight = layer.weight.detach().cpu()
            scale = weight.abs().mean(dim=1, keepdim=True).clamp_min(1e-6)
            codes = torch.clamp(torch.round(weight / scale), min=-1, max=1).to(torch.int8)
            self.register_buffer("codes", codes)
            self.register_buffer("scale", scale.squeeze(1).float())
            if layer.bias is None:
                self.bias = None
            else:
                self.register_buffer("bias", layer.bias.detach().cpu().float())

        def forward(self, x):
            weight = self.codes.to(x.dtype) * self.scale.to(x.dtype).unsqueeze(1)
            bias = None if self.bias is None else self.bias.to(x.dtype)
            return F.linear(x, weight, bias)

    def convert(module: nn.Module) -> nn.Module:
        if isinstance(module, BitLinear):
            return FrozenTernaryLinear(module)
        if isinstance(module, nn.Sequential):
            return nn.Sequential(*(convert(child) for child in module))
        if isinstance(module, nn.Dropout):
            return nn.Identity()
        if isinstance(module, nn.LayerNorm):
            clone = nn.LayerNorm(module.normalized_shape, eps=module.eps, elementwise_affine=module.elementwise_affine)
            clone.load_state_dict({k: v.detach().cpu() for k, v in module.state_dict().items()})
            return clone
        if isinstance(module, nn.GELU):
            return nn.GELU()
        return module

    class EdgeModel(nn.Module):
        def __init__(self, trained: PhysicsBitMLP):
            super().__init__()
            self.backbone = convert(trained.backbone)
            self.head = convert(trained.head)

        def forward(self, x):
            return self.head(self.backbone(x))

    return EdgeModel(model).eval()


def main() -> None:
    set_seed(SEED)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if torch.cuda.is_available():
        torch.backends.cuda.matmul.allow_tf32 = True

    print(f"Loading: {CSV_PATH}")
    df = pd.read_csv(CSV_PATH)
    if MAX_ROWS > 0:
        df = df.sample(n=min(MAX_ROWS, len(df)), random_state=SEED).reset_index(drop=True)

    train_idx, val_idx = train_val_split(len(df))
    feature_df, metadata = prepare_features(df, train_idx)
    train_ds, val_ds, target_stats = make_datasets(df, feature_df, train_idx, val_idx)
    metadata.update(
        {
            "target_safe": TARGET_SAFE,
            "target_regression": TARGET_REGRESSION,
            "target_stats": target_stats,
            "input_dim": len(metadata["feature_columns"]),
            "hidden_dim": HIDDEN_DIM,
            "depth": DEPTH,
            "dropout": DROPOUT,
        }
    )

    train_loader = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True, num_workers=2, pin_memory=torch.cuda.is_available())
    val_loader = DataLoader(val_ds, batch_size=BATCH_SIZE * 2, shuffle=False, num_workers=2, pin_memory=torch.cuda.is_available())

    model = PhysicsBitMLP(len(metadata["feature_columns"]), HIDDEN_DIM, DEPTH, DROPOUT).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=max(1, EPOCHS))

    safe_train = df.iloc[train_idx][TARGET_SAFE].astype("float32")
    pos = float(safe_train.sum())
    neg = float(len(safe_train) - pos)
    pos_weight = torch.tensor([neg / max(pos, 1.0)], device=device)
    scaler = torch.amp.GradScaler("cuda", enabled=torch.cuda.is_available())

    best_state = None
    best_loss = float("inf")
    print(f"Training rows={len(train_ds):,}, val rows={len(val_ds):,}, input_dim={metadata['input_dim']}, device={device}")

    for epoch in range(1, EPOCHS + 1):
        model.train()
        running = 0.0
        batches = 0
        for xb, y_safe, y_reg in train_loader:
            xb = xb.to(device, non_blocking=True)
            y_safe = y_safe.to(device, non_blocking=True)
            y_reg = y_reg.to(device, non_blocking=True)
            optimizer.zero_grad(set_to_none=True)

            with torch.amp.autocast("cuda", enabled=torch.cuda.is_available()):
                out = model(xb)
                loss_cls = F.binary_cross_entropy_with_logits(out[:, :1], y_safe, pos_weight=pos_weight)
                loss_reg = F.smooth_l1_loss(out[:, 1:], y_reg)
                loss = loss_cls + 0.75 * loss_reg

            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            scaler.step(optimizer)
            scaler.update()
            running += float(loss.detach().cpu())
            batches += 1

        scheduler.step()
        metrics = evaluate(model, val_loader, device, target_stats)
        print(
            f"Epoch {epoch:02d}/{EPOCHS} | train_loss={running / max(batches, 1):.4f} | "
            f"val_loss={metrics['loss']:.4f} | safe_acc={metrics['safe_acc']:.4f} | "
            f"cost_mae=₹{metrics['cost_mae_inr']:,.0f} | carbon_mae={metrics['carbon_mae_kg']:,.1f} kg"
        )
        if metrics["loss"] < best_loss:
            best_loss = metrics["loss"]
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}

    if best_state is not None:
        model.load_state_dict(best_state)

    full_path = OUT_DIR / "physics_bitmlp_realvalued_train_state.pt"
    edge_path = OUT_DIR / "physics_bitmlp_ternary_edge.pt"
    script_path = OUT_DIR / "physics_bitmlp_torchscript.pt"
    meta_path = OUT_DIR / "physics_feature_metadata.json"

    torch.save({"model_state_dict": model.state_dict(), "metadata": metadata}, full_path)
    torch.save(export_ternary(model.cpu().eval(), metadata), edge_path)
    traceable = make_traceable_edge_model(model.cpu().eval())
    torch.jit.trace(traceable, torch.zeros(1, metadata["input_dim"])).save(str(script_path))
    meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    print(f"Saved: {full_path}")
    print(f"Saved: {edge_path}")
    print(f"Saved: {script_path}")
    print(f"Saved: {meta_path}")


if __name__ == "__main__":
    main()
