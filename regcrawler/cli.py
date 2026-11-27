"""主控整合入口：python -m regcrawler.cli --out output

流程：
  subtask1  讀種子清單 → 各來源模組擷取（五層備援）→ 與前次快照比對（公布日／修正日／施行日、SHA-512）
            → 首次無快照時依法規沿革抓上一版作為基準線 → 寫 subtask1 Excel、存快照
  subtask2  有變動者 → AST 四階段比對（Lexing/Parsing/Traversal/Code Generation）
            → 語意規則（罰則加重、通知／通報時限縮短）→ 連動影響 → 寫 subtask2 Excel
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib
import logging
import sys
import traceback
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from .models import (ANTI_BOT_FAIL_NOTE, DIFF_ADDED, DIFF_DELETED, DIFF_LINKAGE, DIFF_MODIFIED,
                     STATUS_CURRENT, DiffRecord, LawDocument, SeedEntry)
from .normalize import article_key, content_hash, date_tuple

log = logging.getLogger("regcrawler")
TPE = ZoneInfo("Asia/Taipei")
SOURCE_MODULES = ("moj", "moda", "fsc", "twse", "tpex", "intl")
ROOT = Path(__file__).resolve().parent.parent


def load_seeds(path: Path) -> list[SeedEntry]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out = []
    for item in data.get("laws", []) or []:
        fields = {k: v for k, v in item.items() if k in SeedEntry.__dataclass_fields__}
        out.append(SeedEntry(**fields))
    return out


def _failed_doc(entry: SeedEntry, source_name: str, note: str) -> LawDocument:
    return LawDocument(name=entry.name, source_name=source_name, source_url=entry.url or entry.verified_by,
                       status=entry.status, doc_type=entry.doc_type, category=entry.category,
                       parent_law=entry.parent_law, metadata_only=entry.metadata_only, fetch_note=note)


def run_sources(seeds: list[SeedEntry], fetcher, only: set[str] | None):
    """回傳 [(doc, entry|None, module)]。單一模組失敗不影響其他模組。"""
    results = []
    for modname in SOURCE_MODULES:
        if only and modname not in only:
            continue
        entries = [e for e in seeds if e.source == modname]
        if not entries:
            continue
        try:
            mod = importlib.import_module(f"regcrawler.sources.{modname}")
        except Exception as exc:  # 模組本身載入失敗
            log.exception("載入來源模組 %s 失敗", modname)
            for e in entries:
                results.append((_failed_doc(e, modname, f"來源模組載入失敗：{exc}"), e, None))
            continue
        try:
            docs = mod.fetch(entries, fetcher)
        except Exception as exc:
            log.error("來源模組 %s 執行失敗：%s", modname, traceback.format_exc())
            docs = [_failed_doc(e, getattr(mod, "SOURCE_NAME", modname), f"擷取程式例外：{exc}") for e in entries]
        by_name = {e.name: e for e in entries}
        for d in docs:
            entry = by_name.get(d.name)
            # 種子設定的狀態、類別、母法補入（來源模組未填時）
            if entry:
                d.status = d.status or entry.status
                d.category = d.category or entry.category
                d.parent_law = d.parent_law or entry.parent_law
            results.append((d, entry, mod))
    return results


def _ids(diffs, change):
    return "、".join(d.new_no or d.old_no for d in diffs if d.change == change)


def _metadata_changes(prev: LawDocument | None, doc: LawDocument) -> str:
    if prev is None:
        return ""
    parts = []
    for k in sorted(set(prev.metadata) | set(doc.metadata)):
        a, b = prev.metadata.get(k, "NA"), doc.metadata.get(k, "NA")
        if str(a) != str(b):
            parts.append(f"{k}：{a} → {b}")
    return "；".join(parts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="資安與個資法規知識庫全自動爬蟲 + AST-level Legal Diff")
    ap.add_argument("--config", default=str(ROOT / "config" / "seed_laws.yaml"))
    ap.add_argument("--out", default="output")
    ap.add_argument("--sources", default="", help="逗號分隔，只執行指定來源，例如 moj,fsc")
    ap.add_argument("--force-baseline", action="store_true", help="忽略既有快照，一律以法規沿革前一版為基準")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    import os
    if os.environ.get("REGCRAWL_FORCE_BASELINE", "0").strip().lower() in ("1", "true", "yes"):
        args.force_baseline = True
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    from .fetch.chain import Fetcher
    from . import store
    from .excel_crawl import write_crawl_excel
    from .excel_diff import write_diff_excel
    from .legal_ast.diff import diff_laws
    from .legal_ast.codegen import render_rationale
    from .rules.semantic import classify
    from .rules.linkage import detect

    now = dt.datetime.now(TPE)
    run_id = now.strftime("%Y%m%d-%H%M%S")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    seeds = load_seeds(Path(args.config))
    only = {s.strip() for s in args.sources.split(",") if s.strip()} or None
    fetcher = Fetcher()
    fetched = run_sources(seeds, fetcher, only)

    crawl_rows, diff_records = [], []
    changes_by_law: dict[str, list] = {}
    all_docs: list[LawDocument] = []
    row_index: dict[str, int] = {}

    for doc, entry, mod in fetched:
        all_docs.append(doc)
        row = {
            "法規名稱": doc.name, "法規狀態": doc.status or STATUS_CURRENT,
            "公布日": doc.promulgated_date, "修正日": doc.amended_date, "施行日": doc.effective_date,
            "本次新增": "", "本次刪除": "", "本次變動": "",
            "資料來源": doc.source_url or doc.source_name,
            "取得方式": doc.fetch_method, "SHA-512": "", "備註": doc.fetch_note,
        }
        if doc.fetch_method == "wayback":
            import re as _re
            m = _re.search(r"Wayback 快照 \d{14}", doc.fetch_note or "")
            label = m.group(0) if m else "Wayback 快照"
            row["資料來源"] = f"{row['資料來源']}（{label}）"
        # metadata_only 文件：metadata 可能只有「NA」佔位，以是否有成功取得方式判斷
        if doc.metadata_only and not doc.fetch_method:
            doc.metadata = {}
        if doc.metadata_only and str(doc.metadata.get("狀態", "")).lower().startswith("withdrawn"):
            row["備註"] = "；".join(x for x in (row["備註"], f"官方狀態：{doc.metadata.get('狀態')}") if x)
        if not doc.ok:
            # 反爬失敗時，備註必須「完全等於」規定字串；來源模組附加的細節改寫入執行紀錄（log）
            if not row["備註"] or ANTI_BOT_FAIL_NOTE in row["備註"]:
                if row["備註"] and row["備註"] != ANTI_BOT_FAIL_NOTE:
                    log.info("反爬失敗細節（%s）：%s", doc.name, row["備註"])
                row["備註"] = ANTI_BOT_FAIL_NOTE
            crawl_rows.append(row)
            continue

        row["SHA-512"] = content_hash(doc)
        prev = None if args.force_baseline else store.load_previous(doc.name, doc.source_name, run_id)
        baseline_note = ""
        if prev is None and mod is not None and entry is not None and hasattr(mod, "fetch_previous"):
            try:
                prev = mod.fetch_previous(entry, doc, fetcher)
            except Exception as exc:
                log.warning("fetch_previous 失敗 %s：%s", doc.name, exc)
                prev = None
            baseline_note = ("首次執行：以法規沿革之前一版本為比對基準" if prev is not None
                             else "首次執行：無法取得前一版本，本次建立基準快照")
        if baseline_note:
            row["備註"] = "；".join(x for x in (row["備註"], baseline_note) if x)

        changed = prev is None or content_hash(prev) != row["SHA-512"] or date_tuple(prev) != date_tuple(doc)

        if doc.metadata_only:
            row["本次變動"] = _metadata_changes(prev, doc)
        elif prev is not None and changed:
            diffs = [d for d in diff_laws(prev, doc) if d.change != "未變動"]
            row["本次新增"] = _ids(diffs, DIFF_ADDED)
            row["本次刪除"] = _ids(diffs, DIFF_DELETED)
            row["本次變動"] = _ids(diffs, DIFF_MODIFIED)
            if date_tuple(prev) != date_tuple(doc) and not diffs:
                row["本次變動"] = "日期異動（條文內容未變）"
            changes_by_law[doc.name] = diffs
            for d in diffs:
                findings = classify(d.old_text, d.new_text)
                types = [d.change] + [t for t, _ in findings]
                diff_records.append(DiffRecord(
                    law_name=doc.name, law_status=doc.status, article_no=d.new_no or d.old_no,
                    diff_types=types, old_text=d.old_text, new_text=d.new_text, inline_diff=d.inline_diff,
                    rationale=render_rationale(d, findings), source=doc.source_url or doc.source_name))

        store.save_snapshot(doc, run_id)
        row_index[doc.name] = len(crawl_rows)
        crawl_rows.append(row)

    # 連動影響
    linkages = detect(changes_by_law, all_docs) if changes_by_law else []
    for lk in linkages:
        diff_records.append(DiffRecord(
            law_name=lk.affected_law, law_status="", article_no=lk.affected_article,
            diff_types=[DIFF_LINKAGE], old_text="", new_text="", inline_diff="",
            linkage=f"所依據之母法條文：{lk.parent_article}（{lk.parent_change}）",
            rationale=lk.explanation, source=""))
        parent_key = article_key(lk.parent_article.split("法", 1)[-1]) if "條" in lk.parent_article else ""
        for r in diff_records:  # 只在母法「被引用的那一條」標註受影響的子法
            if (r.law_name != lk.affected_law and lk.parent_article.startswith(r.law_name)
                    and article_key(r.article_no) == parent_key):
                r.linkage = "；".join(x for x in (r.linkage, f"影響：{lk.affected_law} {lk.affected_article}") if x)
        if lk.affected_law in row_index:
            rr = crawl_rows[row_index[lk.affected_law]]
            rr["備註"] = "；".join(x for x in (rr["備註"], f"連動影響：{lk.parent_article} {lk.parent_change}") if x)

    stamp = now.strftime("%Y%m%d")
    p1 = out / f"法規爬蟲結果_{stamp}.xlsx"
    p2 = out / f"AST條文差異比對_{stamp}.xlsx"
    write_crawl_excel(crawl_rows, p1)
    write_diff_excel(diff_records, linkages, p2)
    log.info("完成：%s、%s（法規 %d 筆、差異 %d 筆、連動 %d 筆）", p1, p2, len(crawl_rows), len(diff_records), len(linkages))
    return 0


if __name__ == "__main__":
    sys.exit(main())
