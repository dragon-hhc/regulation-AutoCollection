"""#4 快照儲存與前版讀取（SPEC §7.5）。

路徑：snapshots/<來源安全名>/<法規名安全檔名（保留中文）>/<run_id>.json
run_id：YYYYMMDD-HHMMSS（Asia/Taipei）。字串排序即時間排序。
快照根目錄預設為 repo 根目錄下的 snapshots/，可用環境變數 REGCRAWL_SNAPSHOT_DIR 或各函式的 root 參數覆寫。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import LawDocument
from .normalize import content_hash

TPE = timezone(timedelta(hours=8))  # Asia/Taipei
RUN_ID_RE = re.compile(r"^\d{8}-\d{6}$")
SCHEMA_VERSION = 1
_DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "snapshots"
_MAX_NAME_BYTES = 180


def snapshot_root(root: str | Path | None = None) -> Path:
    if root is not None:
        return Path(root)
    env = os.environ.get("REGCRAWL_SNAPSHOT_DIR")
    return Path(env) if env else _DEFAULT_ROOT


def new_run_id(now: datetime | None = None) -> str:
    now = now or datetime.now(TPE)
    if now.tzinfo is None:
        now = now.replace(tzinfo=TPE)
    return now.astimezone(TPE).strftime("%Y%m%d-%H%M%S")


def safe_name(s: str) -> str:
    """檔名安全化：保留中文與原字形（只做 NFC，不做 NFKC），
    替換路徑／Windows 保留字元與控制字元，過長時截斷並加短 hash 以保唯一。"""
    t = unicodedata.normalize("NFC", s or "").strip()
    t = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', "_", t)
    t = re.sub(r"\s+", " ", t).strip(" .")
    if not t:
        t = "_"
    if len(t.encode("utf-8")) > _MAX_NAME_BYTES:
        h = hashlib.sha1(s.encode("utf-8")).hexdigest()[:10]
        b = t.encode("utf-8")[: _MAX_NAME_BYTES - 11]
        t = b.decode("utf-8", errors="ignore").rstrip(" .") + "_" + h
    return t


def _law_dir(name: str, source_name: str, root: str | Path | None) -> Path:
    return snapshot_root(root) / safe_name(source_name) / safe_name(name)


def save_snapshot(doc: LawDocument, run_id: str, *, root: str | Path | None = None) -> Path:
    if not RUN_ID_RE.match(run_id):
        raise ValueError(f"run_id 格式須為 YYYYMMDD-HHMMSS：{run_id!r}")
    d = _law_dir(doc.name, doc.source_name, root)
    d.mkdir(parents=True, exist_ok=True)
    fetched_at = doc.fetched_at or datetime.now(TPE).isoformat(timespec="seconds")
    payload = {
        "schema": SCHEMA_VERSION,
        "run_id": run_id,
        "ok": doc.ok,
        "content_hash": content_hash(doc) if doc.ok else "",
        "fetched_at": fetched_at,
        "document": doc.to_dict(),
    }
    payload["document"]["fetched_at"] = fetched_at
    path = d / f"{run_id}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return path


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_previous(name: str, source_name: str, before_run_id: str | None = None, *,
                  root: str | Path | None = None) -> LawDocument | None:
    """同一法規在 before_run_id 之前（不含）最近一次「成功」的快照；before_run_id=None 表示不限。"""
    d = _law_dir(name, source_name, root)
    if not d.is_dir():
        return None
    runs = sorted((p for p in d.glob("*.json") if RUN_ID_RE.match(p.stem)), key=lambda p: p.stem, reverse=True)
    for p in runs:
        if before_run_id is not None and p.stem >= before_run_id:
            continue
        data = _read(p)
        if not data or "document" not in data:
            continue
        try:
            doc = LawDocument.from_dict(data["document"])
        except (TypeError, KeyError):
            continue
        if doc.ok:
            return doc
    return None


def list_runs(*, root: str | Path | None = None) -> list[str]:
    """所有快照中出現過的 run_id（升冪）。"""
    base = snapshot_root(root)
    if not base.is_dir():
        return []
    return sorted({p.stem for p in base.glob("*/*/*.json") if RUN_ID_RE.match(p.stem)})


__all__ = ["save_snapshot", "load_previous", "list_runs", "new_run_id", "safe_name", "snapshot_root"]
