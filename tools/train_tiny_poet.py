"""Train/export Tiny Inference's original-corpus, int8 recurrent poetry model."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F


APP = Path(__file__).resolve().parents[1] / "badge/apps/tiny_inference"
sys.path.insert(0, str(APP))
SPEC = importlib.util.spec_from_file_location("poetry_contract", APP / "poet.py")
poet = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(poet)
GRAMMAR = (0, 1, 2, 3, 0, 1)
CATEGORIES = ((0, 16, 4), (16, 40, 6), (40, 56, 4), (56, 64, 0))


def quantized(value, scale):
    scaled = torch.clamp(value * scale, -128, 127)
    rounded = torch.sign(scaled) * torch.floor(torch.abs(scaled) + 0.5)
    return value + (rounded / scale - value).detach()


class Network(nn.Module):
    def __init__(self):
        super().__init__()
        self.recurrent = nn.Parameter(torch.randn(poet.HIDDEN, poet.VOCABULARY + poet.HIDDEN + 1) * 0.1)
        self.readout = nn.Parameter(torch.randn(poet.VOCABULARY, poet.HIDDEN + 1) * 0.1)

    def forward(self, tokens):
        hidden = torch.zeros(tokens.shape[1], poet.HIDDEN)
        one = torch.ones(tokens.shape[1], 1)
        recurrent = quantized(self.recurrent, 64)
        readout = quantized(self.readout, 32)
        logits = []
        for token in tokens:
            inputs = torch.cat((F.one_hot(token, poet.VOCABULARY).float(), hidden, one), dim=1)
            hidden = torch.clamp(quantized(F.linear(inputs, recurrent), 64), -1, 1)
            logits.append(quantized(F.linear(torch.cat((hidden, one), dim=1), readout), 16))
        return torch.stack(logits)


def batch(rng, size):
    """Original synthetic couplets; no external poems or pretrained weights."""
    theme = rng.integers(0, 4, size=size)
    targets = []
    for line in range(2):
        for category in GRAMMAR:
            low, high, per_theme = CATEGORIES[category]
            if per_theme:
                groups = np.where(rng.random(size) < 0.9, theme, rng.integers(0, 4, size=size))
                words = low + groups * per_theme + rng.integers(0, per_theme, size=size)
            else:
                words = rng.integers(low, high, size=size)
            targets.append(words)
        targets.append(np.full(size, poet.LINE if line == 0 else poet.END))
    target = torch.tensor(np.stack(targets), dtype=torch.long)
    inputs = torch.cat((torch.full((1, size), poet.START), target[:-1]), dim=0)
    return inputs, target


def category(tokens):
    result = torch.zeros_like(tokens)
    for index, (low, high, _) in enumerate(CATEGORIES):
        result[(tokens >= low) & (tokens < high)] = index
    result[tokens == poet.LINE] = 4
    result[tokens == poet.END] = 5
    result[tokens >= poet.START] = 6
    return result


def weight_bytes(tensor, scale):
    values = tensor.detach().numpy()
    integers = np.sign(values) * np.floor(np.abs(values) * scale + 0.5)
    integers = np.clip(integers, -128, 127).astype(np.int8)
    return np.pad(integers, ((0, 0), (0, 3))).tobytes()


def verify_export(network, payload):
    """Require the shipped integer equations to reproduce the training graph."""
    weights = np.frombuffer(payload[len(poet.MODEL_HEADER):], dtype=np.int8).astype(np.int32)
    split = poet.HIDDEN * poet.COLUMNS
    recurrent = weights[:split].reshape(poet.HIDDEN, poet.COLUMNS)
    readout = weights[split:].reshape(poet.VOCABULARY, poet.OUTPUT_COLUMNS)
    inputs, _ = batch(np.random.default_rng(7102), 32)
    with torch.no_grad():
        expected = network(inputs).numpy() * 16
    hidden = np.zeros((32, poet.HIDDEN), dtype=np.int32)

    def requantize(values, shift):
        rounded = np.sign(values) * ((np.abs(values) + (1 << (shift - 1))) >> shift)
        return np.clip(rounded, -128, 127)

    for step, tokens in enumerate(inputs.numpy()):
        features = np.zeros((32, poet.COLUMNS), dtype=np.int32)
        features[np.arange(32), tokens] = 64
        features[:, poet.VOCABULARY:poet.VOCABULARY + poet.HIDDEN] = hidden
        features[:, poet.VOCABULARY + poet.HIDDEN] = 64
        hidden = np.clip(requantize(features @ recurrent.T, 6), -64, 64)
        outputs = np.zeros((32, poet.OUTPUT_COLUMNS), dtype=np.int32)
        outputs[:, :poet.HIDDEN] = hidden
        outputs[:, poet.HIDDEN] = 64
        logits = requantize(outputs @ readout.T, 7)
        if not np.array_equal(logits, expected[step]):
            raise RuntimeError("exported integer model differs from its quantized training graph")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--steps", type=int, default=1600)
    parser.add_argument("--seed", type=int, default=20261028)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--output", type=Path, default=APP / "assets")
    args = parser.parse_args()
    if args.steps < 1 or args.batch_size < 1 or args.threads < 1:
        parser.error("steps, batch size and threads must be positive")
    torch.set_num_threads(args.threads)
    torch.manual_seed(args.seed)
    torch.use_deterministic_algorithms(True)
    rng = np.random.default_rng(args.seed)
    network = Network()
    optimizer = torch.optim.Adam(network.parameters(), lr=0.006)
    for step in range(args.steps):
        inputs, target = batch(rng, args.batch_size)
        logits = network(inputs)
        loss = F.cross_entropy(logits.reshape(-1, poet.VOCABULARY), target.reshape(-1))
        optimizer.zero_grad()
        loss.backward()
        nn.utils.clip_grad_norm_(network.parameters(), 1)
        optimizer.step()
        with torch.no_grad():
            network.recurrent.clamp_(-2, 127 / 64)
            network.readout.clamp_(-4, 127 / 32)
        if step % 200 == 0 or step + 1 == args.steps:
            print("step=%d loss=%.4f" % (step + 1, loss.item()), flush=True)
    with torch.no_grad():
        inputs, target = batch(np.random.default_rng(args.seed + 1), 512)
        logits = network(inputs)
        loss = F.cross_entropy(logits.reshape(-1, poet.VOCABULARY), target.reshape(-1)).item()
        accuracy = (category(logits.argmax(-1)) == category(target)).float().mean().item()
    if accuracy < 0.98 or loss > 2.5:
        raise RuntimeError("trained model missed held-out quality gate: loss=%.4f category=%.4f"
                           % (loss, accuracy))
    payload = (poet.MODEL_HEADER + weight_bytes(network.recurrent, 64)
               + weight_bytes(network.readout, 32))
    assert len(payload) == len(poet.MODEL_HEADER) + poet.WEIGHT_BYTES
    verify_export(network, payload)
    metadata = {
        "format": "tinypoet-v1",
        "architecture": "elman-hard-tanh-word-rnn",
        "hidden": poet.HIDDEN, "vocabulary": poet.VOCABULARY,
        "trainable_parameters": sum(parameter.numel() for parameter in network.parameters()),
        "weight_bytes": poet.WEIGHT_BYTES,
        "model_sha256": hashlib.sha256(payload).hexdigest(),
        "vocabulary_sha256": poet.vocabulary_digest(),
        "quantization": {"recurrent_weight_q": 6, "hidden_q": 6,
                         "readout_weight_q": 5, "logits_q": 4,
                         "rounding": "nearest-ties-away-from-zero"},
        "training": {
            "corpus": "original-synthetic-couplets-v1",
            "description": "Original 64-word banks; adjective noun verb preposition adjective noun, twice; no downloaded text.",
            "seed": args.seed, "steps": args.steps, "batch_size": args.batch_size,
            "poems_seen": args.steps * args.batch_size,
            "torch": torch.__version__, "numpy": np.__version__,
            "held_out_poems": 512, "held_out_cross_entropy": round(loss, 6),
            "held_out_argmax_category_accuracy": round(accuracy, 6),
        },
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "poet.bin").write_bytes(payload)
    (args.output / "poet.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    print(json.dumps(metadata, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
