import datetime
import json
import os
import sqlite3
import threading

import numpy as np
import torch
import torch.nn as nn


class ActionSequenceModel(nn.Module):
    """Sequence model that learns patterns in the user's action history:
    given a recent window of (app, tool, outcome) actions, predict the next
    tool to use. Uses a small GRU — fast on CPU and robust on small datasets."""

    def __init__(self, vocab_app, vocab_tool, vocab_outcome,
                 d_model=48, n_layers=2, max_len=16):
        super().__init__()
        self.vocab_app = vocab_app            # dict str->idx
        self.vocab_tool = vocab_tool
        self.vocab_outcome = vocab_outcome
        self.n_tools = len(vocab_tool)
        self.max_len = max_len

        self.emb_app = nn.Embedding(len(vocab_app) + 2, d_model)
        self.emb_tool = nn.Embedding(len(vocab_tool) + 2, d_model)
        self.emb_out = nn.Embedding(len(vocab_outcome) + 2, d_model)
        self.gru = nn.GRU(d_model, d_model, num_layers=n_layers, batch_first=True)
        self.head = nn.Linear(d_model, len(vocab_tool) + 1)  # +1 for padding/unknown

    def _token(self, vocab, key, pad_offset=0):
        idx = vocab.get(key)
        if idx is None:
            idx = len(vocab)  # unknown token
        return idx + pad_offset

    def encode_seq(self, seq):
        """seq: list of (app, tool, outcome) tuples -> (max_len, 3) long tensor."""
        L = len(seq)
        if L > self.max_len:
            seq = seq[-self.max_len:]
            L = len(seq)
        tok = torch.zeros((self.max_len, 3), dtype=torch.long)
        for i, (app, tool, outcome) in enumerate(seq):
            tok[L - 1 - i, 0] = self._token(self.vocab_app, app)
            tok[L - 1 - i, 1] = self._token(self.vocab_tool, tool)
            tok[L - 1 - i, 2] = self._token(self.vocab_outcome, outcome)
        return tok, L

    def forward(self, seq):
        tok, L = self.encode_seq(seq)
        x = self.emb_app(tok[:, 0]) + self.emb_tool(tok[:, 1]) + \
            self.emb_out(tok[:, 2])
        out, _ = self.gru(x.unsqueeze(0))  # (1, max_len, d_model)
        return self.head(out.squeeze(0))   # (max_len, n_tools+1)


class ActionLearner:
    """Neural action-learning circuit. Trains a small Transformer on the
    recorded experience data, then predicts the user's most likely next action
    for a given screen/app context. Feeds its instincts into DUDE's planner."""

    def __init__(self, data_dir=None, weights_path=None):
        if data_dir is None:
            from core.config import get_config
            data_dir = get_config().data_dir
        self.data_dir = data_dir
        self.weights_path = weights_path or os.path.join(data_dir, "action_model.pt")
        self.db = os.path.join(data_dir, "experience.db")
        self.vocab_app = {"unknown": 0}
        self.vocab_tool = {"unknown": 0}
        self.vocab_outcome = {"OK": 0, "FAIL": 1, "MISS": 2, "SKIP": 3, "unknown": 4}
        self.model = None
        self._load_vocab()
        self._lock = threading.Lock()

    # ---------- vocabulary from data ----------
    def _load_vocab(self):
        try:
            c = sqlite3.connect(self.db).cursor()
            for (tool,) in c.execute("SELECT DISTINCT tool FROM experiences").fetchall():
                self.vocab_tool[tool] = len(self.vocab_tool)
            for (app,) in c.execute("SELECT DISTINCT app FROM experiences").fetchall():
                a = app or "unknown"
                if a not in self.vocab_app:
                    self.vocab_app[a] = len(self.vocab_app)
        except Exception:
            pass

    # ---------- dataset ----------
    def _sequences(self, window=10):
        c = sqlite3.connect(self.db).cursor()
        rows = c.execute(
            "SELECT app, tool, outcome FROM experiences "
            "WHERE tool IS NOT NULL ORDER BY id ASC LIMIT 3000").fetchall()
        seq = []
        for app, tool, outcome in rows:
            seq.append((app or "unknown", tool or "unknown", outcome or "unknown"))
        # Build overlapping windows (X) -> next tool label (y)
        X, y = [], []
        for i in range(len(seq) - 1):
            start = max(0, i - window + 1)
            X.append(seq[start:i + 1])
            y.append(seq[i + 1][1])  # next tool
        return X, y, seq

    def _build_datasets(self, window=10, split=0.8):
        X, y, _ = self._sequences(window)
        n = len(X)
        if n < 4:
            return None
        cut = int(n * split)
        Xtr, ytr, Xva, yva = X[:cut], y[:cut], X[cut:], y[cut:]
        # pad sequences to model window
        pad = window
        yidx = [self.vocab_tool.get(t, len(self.vocab_tool)) for t in ytr]
        yyv = [self.vocab_tool.get(t, len(self.vocab_tool)) for t in yva]
        return (Xtr, yidx, Xva, yyv, pad)

    # ---------- training ----------
    def train(self, epochs=30, window=12, lr=1e-3, batch=64):
        ds = self._build_datasets(window)
        if ds is None:
            return "not enough data to train"
        Xtr, ytr, Xva, yva, pad = ds
        self.model = ActionSequenceModel(
            self.vocab_app, self.vocab_tool, self.vocab_outcome, max_len=pad)
        optimizer = torch.optim.Adam(self.model.parameters(), lr=lr)
        lossf = nn.CrossEntropyLoss(ignore_index=len(self.vocab_tool))
        n = len(Xtr)
        best_va = 1e9
        for ep in range(epochs):
            self.model.train()
            perm = np.random.permutation(n)
            total = 0.0
            nbatch = 0
            for b in range(0, n, batch):
                idxs = perm[b:b + batch]
                xs = []
                ys = []
                for i in idxs:
                    seq = Xtr[i][-window:]
                    tok, L = self.model.encode_seq(seq)
                    xs.append(tok)
                    ys.append(ytr[i])
                xb = torch.stack(xs)
                yb = torch.tensor(ys)
                Eb = self.model.emb_app(xb[:, :, 0]) + self.model.emb_tool(xb[:, :, 1]) + \
                    self.model.emb_out(xb[:, :, 2])
                out, _ = self.model.gru(Eb)
                logits = self.model.head(out[:, -1, :])
                loss = lossf(logits, yb)
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
                total += loss.item()
                nbatch += 1
            self.model.eval()
            with torch.no_grad():
                xs = [self.model.encode_seq(Xva[j][-window:])[0] for j in range(len(Xva))]
                if xs:
                    xb = torch.stack(xs)
                    Eb = self.model.emb_app(xb[:, :, 0]) + self.model.emb_tool(xb[:, :, 1]) + \
                        self.model.emb_out(xb[:, :, 2])
                    out, _ = self.model.gru(Eb)
                    logits = self.model.head(out[:, -1, :])
                    yb = torch.tensor(yva)
                    va_loss = lossf(logits, yb).item()
                else:
                    va_loss = best_va
            best_va = min(best_va, va_loss)
        self.save()
        return f"trained {n} sequences / {epochs} epochs, va_loss={best_va:.3f}"

    # ---------- inference ----------
    def predict_next(self, seq, k=5):
        """seq: list of (app, tool, outcome) recent actions -> top-k next tools.
        Returns list of (tool_name, confidence)."""
        if self.model is None or not seq:
            return []
        try:
            self.model.eval()
            with torch.no_grad():
                window = self.model.max_len
                s = seq[-window:]
                out = self.model(s)
                _, L = self.model.encode_seq(s)
                logits = out[L - 1]
                probs = torch.softmax(logits, dim=0)
                top = torch.topk(probs, min(k, logits.size(0)))
                idx2tool = {v: t for t, v in self.vocab_tool.items()}
                res = []
                for i, idx in enumerate(top.indices.tolist()):
                    name = idx2tool.get(idx, "unknown")
                    res.append((name, round(float(top.values[i]), 3)))
                return res
        except Exception:
            return []

    def save(self):
        if self.model is None:
            return
        try:
            torch.save({
                "state": self.model.state_dict(),
                "vocab_app": self.vocab_app,
                "vocab_tool": self.vocab_tool,
                "vocab_outcome": self.vocab_outcome,
                "max_len": self.model.max_len,
            }, self.weights_path)
        except Exception as e:
            print(f"[action-model] save failed: {e}")

    def load(self):
        if not os.path.exists(self.weights_path):
            return False
        try:
            ck = torch.load(self.weights_path, map_location="cpu")
            self.vocab_app, self.vocab_tool, self.vocab_outcome = \
                ck["vocab_app"], ck["vocab_tool"], ck["vocab_outcome"]
            self.model = ActionSequenceModel(
                self.vocab_app, self.vocab_tool, self.vocab_outcome,
                max_len=ck["max_len"])
            self.model.load_state_dict(ck["state"])
            self.model.eval()
            return True
        except Exception as e:
            print(f"[action-model] load failed: {e}")
            return False

    # ---------- context builder for DUDE ----------
    def context_hint(self, recent_app, recent_tools=None, limit=5):
        """Build a short text hint of predicted next actions, from the last few
        tools used in the current app, to feed into DUDE's planner."""
        rows = self._recent_rows(recent_app, recent_tools)
        if not rows:
            return ""
        preds = self.predict_next(rows, k=limit)
        if not preds:
            return ""
        lines = ", ".join(f"{t} ({p:.0%})" for t, p in preds)
        return f"neural instinct -> likely next actions: {lines}"

    def _recent_rows(self, app, recent_tools, n=8):
        seq = []
        if recent_tools:
            for t in recent_tools[-n:]:
                seq.append((app or "unknown", t or "unknown", "OK"))
        if len(seq) < 2:
            try:
                c = sqlite3.connect(self.db).cursor()
                rows = c.execute(
                    "SELECT app, tool, outcome FROM experiences "
                    "WHERE (?='' OR app=?) AND tool IS NOT NULL "
                    "ORDER BY id DESC LIMIT ?", (app, app, n)).fetchall()
                seq = [(a or "unknown", t or "unknown", o or "unknown")
                       for a, t, o in rows][::-1]
            except Exception:
                pass
        return seq
