"""共用資料模型（主控定義，subagent 不得修改；需要新欄位請回報主控）。"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

ANTI_BOT_FAIL_NOTE = "本次搜尋的網頁有 反爬蟲機制，嘗試 5 種不同方法之後仍無法取得資料"
OFFICIAL_API = "official_api"
# 五種反爬對抗方法（全失敗才標註 ANTI_BOT_FAIL_NOTE）；official_api 為優先的官方管道，不計入這 5 種
FETCH_METHODS = ("requests", "requests_alt", "playwright", "playwright_human", "wayback")

STATUS_CURRENT = "現行"
STATUS_DRAFT = "草案"
STATUS_PROPOSAL = "提案"
VALID_STATUS = (STATUS_CURRENT, STATUS_DRAFT, STATUS_PROPOSAL)

NA = "NA"

# 差異類型固定用語（SPEC §6）
DIFF_ADDED = "新增條文"
DIFF_DELETED = "刪除條文"
DIFF_MODIFIED = "修正條文"
DIFF_PENALTY_UP = "罰則加重"
DIFF_NOTICE_SHORTER = "通知時限縮短"
DIFF_REPORT_SHORTER = "通報時限縮短"
DIFF_LINKAGE = "連動影響"


@dataclass
class SeedEntry:
    """config/seed_laws.yaml 的一筆設定。"""
    name: str                      # 法規正式名稱（原文）
    source: str                    # moj | moda | fsc | twse | tpex | intl
    category: str = ""             # 例如 個資法體系、資安法體系、金融監理、銀行業、國際
    status: str = STATUS_CURRENT
    doc_type: str = "法規"          # 法規 | 函釋 | 指引 | 規章 | 標準 | 草案
    url: str = ""                  # 已查證之官方頁面（可空，由來源模組自行定位）
    ids: dict = field(default_factory=dict)   # 來源專屬識別碼，例如 {"pcode": "I0050021"}
    parent_law: str = ""           # 授權母法名稱（連動偵測用）
    metadata_only: bool = False
    include_interpretations: bool = False      # 是否一併抓取相關函釋
    verified_by: str = ""          # 查證來源 URL（禁止杜撰的佐證）
    note: str = ""


@dataclass
class Article:
    no: str                        # 原文條號
    text: str                      # 原文內容，項以 \n 分隔
    chapter: str = ""              # 所屬編章節（原文），可空


@dataclass
class LawDocument:
    name: str
    source_name: str               # 例如「法務部全國法規資料庫」
    source_url: str
    status: str = STATUS_CURRENT
    doc_type: str = "法規"
    promulgated_date: str = NA     # 公布日（原文）
    amended_date: str = NA         # 修正日（原文）
    effective_date: str = NA       # 施行日（原文，例如「未定（由行政院定之）」）
    history: str = ""              # 沿革原文
    articles: list[Article] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)   # metadata_only 文件的後設資料
    metadata_only: bool = False
    fetch_method: str = ""         # 實際成功的方法（FETCH_METHODS 之一）
    fetch_note: str = ""           # 失敗原因、Wayback 時間戳記等
    fetched_at: str = ""           # ISO 8601
    category: str = ""
    parent_law: str = ""
    version_label: str = ""        # 例如「民國 114 年 11 月 11 日」或「ISO/IEC 27701:2025」

    @property
    def ok(self) -> bool:
        return bool(self.articles) or (self.metadata_only and bool(self.metadata))

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "LawDocument":
        d = dict(d)
        d["articles"] = [Article(**a) for a in d.get("articles", [])]
        return LawDocument(**d)


@dataclass
class FetchResult:
    ok: bool
    url: str
    content: bytes = b""
    method_used: str = ""
    attempts: list[dict] = field(default_factory=list)   # [{"method":..., "ok":..., "error":...}]
    note: str = ""
    final_url: str = ""
    wayback_timestamp: str = ""

    @property
    def text(self) -> str:
        for enc in ("utf-8", "big5", "cp950"):
            try:
                return self.content.decode(enc)
            except UnicodeDecodeError:
                continue
        return self.content.decode("utf-8", errors="replace")


@dataclass
class ArticleDiff:
    """#5 AST 引擎輸出的條文層級差異（#6 規則與連動偵測以此為輸入）。"""
    key: str                       # normalize.article_key
    old_no: str                    # 原文條號（舊），新增時為 ""
    new_no: str                    # 原文條號（新），刪除時為 ""
    change: str                    # DIFF_ADDED | DIFF_DELETED | DIFF_MODIFIED | "未變動"
    old_text: str = ""
    new_text: str = ""
    node_changes: list[str] = field(default_factory=list)   # 例如「第2項第1款 修正」「第3項 新增」
    inline_diff: str = ""          # 逐字差異，格式 [-刪除文字-]{+新增文字+}
    rationale: list[str] = field(default_factory=list)      # AST 依據（各步驟說明）


@dataclass
class DiffRecord:
    """subtask2 一列。"""
    law_name: str
    law_status: str
    article_no: str                # 原文條號（新版優先，刪除時用舊版）
    diff_types: list[str]
    old_text: str
    new_text: str
    inline_diff: str               # 逐字差異標記，例如 [-刪-]{+增+}
    linkage: str = ""
    rationale: str = ""            # 本次 AST 判斷新舊條文差異的依據及 rationale
    source: str = ""


@dataclass
class LinkageRecord:
    affected_law: str
    affected_article: str
    parent_article: str
    parent_change: str
    explanation: str
