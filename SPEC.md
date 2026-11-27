# 主控統一規格（Orchestrator Spec）v1.0

本文件是所有 subagent 必須遵守的唯一規範。若與個別指示衝突，以本文件為準；有疑義回報主控，不得自行變更介面。

## 0. 最高原則（不可違反）
1. **保留原文**：法規名稱、條文、日期、沿革一律原文照錄，不改寫、不翻譯、不轉簡體、不「修正」錯字。正規化只用於「計算 hash 與比對」，輸出到 Excel 的文字一律是原文。
2. **禁止杜撰**：任何法規名稱、條號、日期、URL，只能來自程式實際抓到的資料，或已經由 WebSearch/WebFetch 在官方來源查證過的設定。查不到就寫「NA」或標註查無，不可猜。
3. **反爬蟲**：每個網頁依序嘗試 5 種方法（見 §5）；5 種都失敗時，該筆的備註欄必須完全等於：
   `本次搜尋的網頁有 反爬蟲機制，嘗試 5 種不同方法之後仍無法取得資料`
   （常數 `ANTI_BOT_FAIL_NOTE`，定義在 `regcrawler/models.py`）。
   不做：CAPTCHA 破解、代理池規避封鎖、繞過登入或存取控制。
4. **開發環境限制**：開發沙箱連不到任何法規網站（只能連 GitHub 與套件庫）。程式要在 GitHub Actions 上才會實際執行。因此：
   - 網址、API 端點、HTML 結構，必須先用 WebSearch / WebFetch 查證，並在程式碼註解中寫明查證來源 URL。
   - 每個來源模組都要有**離線樣本（fixtures）**與單元測試；fixture 內容必須來自實際查到的官方文字（可截短），並在 fixture 檔頭註明來源 URL。不可自己編造條文。

## 1. 目錄結構（各 subagent 只能寫自己負責的檔案）
```
regcrawler/                     專案根目錄（日後就是 GitHub repo 根目錄）
  SPEC.md                       主控
  README.md                     主控
  requirements.txt              主控
  config/seed_laws.yaml         主控建立；#1 #2 #3 只能「新增」自己負責來源的條目
  regcrawler/__init__.py        主控
  regcrawler/models.py          主控（資料模型，不可改；需要新欄位請回報）
  regcrawler/normalize.py       主控（正規化與 SHA-512）
  regcrawler/fetch/chain.py     #4  五層備援擷取鏈
  regcrawler/store.py           #4  快照儲存與前版讀取
  regcrawler/excel_crawl.py     #4  subtask1 Excel
  regcrawler/sources/moj.py     #1  法務部全國法規資料庫
  regcrawler/sources/moda.py    #1  數位發展部資通安全署公告區（含數位部、經濟部、國科會所定辦法之定位）
  regcrawler/sources/fsc.py     #2  金管會主管法規查詢系統（含函令）
  regcrawler/sources/twse.py    #2  證交所法規分享知識庫
  regcrawler/sources/tpex.py    #2  櫃買中心規章
  regcrawler/sources/intl.py    #3  GDPR / EUR-Lex、ISO、BSI
  regcrawler/legal_ast/         #5  lexer.py parser.py diff.py codegen.py
  regcrawler/rules/semantic.py  #6  罰則加重、通知/通報時限縮短
  regcrawler/rules/linkage.py   #6  連動影響偵測（授權依據條文被刪改）
  regcrawler/excel_diff.py      #5  subtask2 Excel
  regcrawler/cli.py             主控（整合）
  .github/workflows/regcrawl.yml #4
  snapshots/                    執行時產生（commit 回 repo）
  tests/                        各自寫 tests/test_<模組>.py；fixtures 放 tests/fixtures/<模組>/
```

## 2. 資料模型（見 regcrawler/models.py）
- `LawDocument`：一部法規（或一份函釋、指引、標準後設資料）的一個版本。
- `Article`：一條條文，`no` 為原文條號字串（例如「第 1-1 條」「第二十條之一」「Article 33」），`text` 為原文內容（項以 `\n` 分隔，保留原文的款、目編號）。
- 日期欄位一律是**字串**，原文照錄：
  - 有資料：照官方格式，例如「民國 114 年 11 月 11 日」或「2016-04-27」。
  - 官方寫明未定：照錄，例如「未定（由行政院定之）」。
  - 完全沒有資料：「NA」。
- `status`：只能是「現行」「草案」「提案」三者之一（常數 `STATUS_*`）。
- `metadata_only=True`：只追蹤後設資料（ISO/BSI 標準），`articles` 為空，比對只比後設資料。

## 3. 來源模組介面（#1 #2 #3 必須實作）
```python
SOURCE_NAME: str   # 例如 "法務部全國法規資料庫"

def fetch(entries: list[SeedEntry], fetcher: Fetcher) -> list[LawDocument]:
    """entries 為 seed_laws.yaml 中 source 等於本模組的條目。
    所有網路存取必須透過 fetcher.get(url, ...)（#4 的五層備援鏈），不可自行 requests.get。
    單筆失敗不可讓整批失敗：回傳一個 LawDocument，articles 空、fetch_note 寫明原因。"""

def fetch_previous(entry: SeedEntry, current: LawDocument, fetcher: Fetcher) -> LawDocument | None:
    """首次執行基準線：依法規沿革抓「上一個版本」全文。抓不到回傳 None 並在 current.fetch_note 註明。"""
```
`Fetcher.get(url, *, method_hint=None, expect="html"|"json"|"xml"|"binary", wayback_ok=True) -> FetchResult`
（`FetchResult.ok / content(bytes) / text / method_used / attempts / note`）

## 4. 正規化與 SHA-512（regcrawler/normalize.py，主控已實作）
`content_hash(doc)` = SHA-512( 正規化後的「法規名稱 + 各條條號與內文」 )。
正規化：Unicode NFKC 以外的處理**不做**全形半形混轉於輸出；hash 用的正規化 = NFKC、去除所有空白與換行、統一條號寫法。
變動判定：公布日、修正日、施行日任一不同，或 hash 不同 → 觸發 subtask2。

## 5. 五層備援擷取鏈（#4）
1. `official_api`：官方 Open API / 開放資料下載（由來源模組以 method_hint 指定 URL）
2. `requests`：一般 HTTP（合理 User-Agent、Accept-Language: zh-TW、重試與指數退避、每網域請求間隔 ≥ 2 秒、遵守 robots.txt 的 Crawl-delay）
3. `playwright`：無頭瀏覽器渲染（處理 JS 載入）
4. `playwright_human`：模擬人為操作（隨機滑鼠移動軌跡、捲動、停頓 1.5–6 秒、真實 viewport 與時區 Asia/Taipei）
5. `wayback`：Internet Archive Wayback Machine（先查 `https://archive.org/wayback/available?url=`，取最近快照；使用快照時 note 必須寫明快照時間戳記，且 Excel 資料來源欄標註「Wayback 快照 YYYYMMDDhhmmss」）
全部失敗 → `note = ANTI_BOT_FAIL_NOTE`。

## 6. 差異類型（subtask2，#5 #6）
固定用語（不可另創）：
- 「新增條文」「刪除條文」「修正條文」「罰則加重」「通知時限縮短」「通報時限縮短」「連動影響」
- 一條可同時有多個類型（例如「修正條文；罰則加重」）。
- 「通知」= 對當事人；「通報」= 對主管機關。依原文用字判斷（條文中出現「通知」或「通報」），不可互換。
- 條號比對以「正規化條號」為鍵（第二十條之一 = 第 20-1 條），但輸出用原文條號。

## 7. Excel 欄位
subtask1（`法規爬蟲結果_YYYYMMDD.xlsx`）：
法規名稱｜法規狀態｜公布日｜修正日｜施行日｜本次新增｜本次刪除｜本次變動｜資料來源｜取得方式｜SHA-512｜備註

subtask2（`AST條文差異比對_YYYYMMDD.xlsx`）：
法規名稱｜法規狀態｜條號｜差異類型｜舊條文｜新條文｜逐字差異｜連動影響｜本次 AST 判斷新舊條文差異的依據及 rationale｜資料來源

連動影響另開一個工作表「連動影響」：受影響法規｜受影響條文｜所依據之母法條文｜母法異動類型｜說明。

## 7.5 模組間介面（主控整合時依此呼叫，不可更改簽名）
- #4 `regcrawler.fetch.chain.Fetcher(timeout=30, min_interval=2.0)`；`.get(url, *, method_hint=None, expect="html", wayback_ok=True) -> FetchResult`
  - **定案（取代 §5 的編號）**：`method_hint="official_api"` 表示該 URL 是官方 Open API／開放資料，先以 `official_api` 下載；失敗或未指定時，一律依序執行下列 **5 種反爬對抗方法**：`requests` → `requests_alt`（不同 UA、HTTP/1.1、關閉壓縮、帶 Referer 為來源首頁、先取首頁 cookie）→ `playwright` → `playwright_human` → `wayback`。這 5 種全失敗才標註 `ANTI_BOT_FAIL_NOTE`。`FetchResult.attempts` 須逐筆記錄。
- #4 `regcrawler.store`：`save_snapshot(doc, run_id) -> path`、`load_previous(name, source_name, before_run_id) -> LawDocument | None`、`list_runs() -> list[str]`；快照路徑 `snapshots/<source>/<安全檔名>/<run_id>.json`。
- #4 `regcrawler.excel_crawl.write_crawl_excel(rows: list[dict], path)`；rows 的 key 即 §7 subtask1 欄名。
- #5 `regcrawler.legal_ast.diff.diff_laws(old: LawDocument | None, new: LawDocument) -> list[ArticleDiff]`（含未變動者，change="未變動"）
- #5 `regcrawler.legal_ast.parser.parse(doc) -> LawNode`；`regcrawler.legal_ast.codegen.render_inline(old, new) -> str`
- #5 `regcrawler.excel_diff.write_diff_excel(records: list[DiffRecord], linkages: list[LinkageRecord], path)`
- #6 `regcrawler.rules.semantic.classify(old_text: str, new_text: str) -> list[tuple[str, str]]`（(差異類型, rationale)，只回傳罰則加重／通知時限縮短／通報時限縮短）
- #6 `regcrawler.rules.linkage.detect(changes: dict[str, list[ArticleDiff]], docs: list[LawDocument]) -> list[LinkageRecord]`（changes 的 key 為母法名稱）
- 來源模組：`regcrawler.sources.<mod>.fetch(entries, fetcher)`、`fetch_previous(entry, current, fetcher)`、`SOURCE_NAME`

## 8. 測試驗收（#6 負責驗收）
個人資料保護法 民國 114 年 11 月 11 日修正（華總一經字第 11400114521 號令），官方沿革：
- 修正：第 1-1、12、18、21、22、23、24、25、26、41、47、48、49、52、53、55 條
- 增訂：第 1-2、20-1、21-1、21-2、21-3、21-4、21-5、51-1、53-1 條
- 刪除：第 27 條
- 連動：「金融監督管理委員會指定非公務機關個人資料檔案安全維護辦法」第 1 條規定「本辦法依個人資料保護法(以下簡稱本法)第二十七條第三項規定訂定之。」→ 母法第 27 條刪除 → 連動影響。
