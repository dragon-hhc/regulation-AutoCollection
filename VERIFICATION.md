# 獨立查核報告（VERIFICATION）

- 查核對象：/home/claude/regcrawler（依 SPEC.md v1.0）
- 查核日期：2026-11-27
- 查核者角色：獨立驗證員（未參與開發；本次只新增本檔，未修改任何其他檔案）
- 方法：靜態閱讀程式碼；執行 `python -m pytest -q`；檢視 /home/claude/sample/*.xlsx；以「全部擷取失敗」的假 Fetcher 實際跑過 6 個來源模組與 `regcrawler.cli.main()` 全流程（腳本放在 repo 外的 /tmp/verif/）；用 WebFetch / WebSearch 逐筆查證法規名稱與 fixture 條文。

## 總結

| # | 項目 | 結論 |
|---|------|------|
| 1 | 禁止杜撰（seed 名稱與 verified_by） | **存疑**：沒有杜撰的名稱，但有 2 筆國際條目不是官方正式標題的逐字文字，另有 7 筆的 verified_by 不是官方來源 |
| 2 | 原文保留 | **通過**（附輕微觀察 5 點） |
| 3 | 反爬註記 | **不通過**：25/38 列的備註不是「完全等於」規定字串 |
| 4 | Excel 欄位 | **通過** |
| 5 | Fixture 來源與逐字比對 | **通過**（附存疑觀察 3 點） |
| 6 | 安全與合規 | **通過**（附存疑：robots.txt Disallow 未落實） |
| 7 | GitHub Actions | **通過** |

合計：通過 5 項，不通過 1 項（#3），存疑 1 項（#1）。

---

## 1. 禁止杜撰：config/seed_laws.yaml 名稱逐筆查證 — 存疑

### 1a. 臺灣法規、規章、指引（36 筆，其中 1 個名稱重複出現在 twse 和 tpex）— 名稱全部逐字相符

| seed 名稱（config/seed_laws.yaml） | 查證來源（實際查到的頁面） | 官方顯示名稱／日期 | 結果 |
|---|---|---|---|
| 個人資料保護法 | https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=I0050021 | 個人資料保護法；修正 民國 114 年 11 月 11 日 | 相符 |
| 個人資料保護法施行細則 | https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=I0050022 | 個人資料保護法施行細則；修正 民國 105 年 03 月 02 日 | 相符（verified_by 不是官方來源，見 1c） |
| 個人資料事故通知通報及應變辦法（草案） | https://www.leeandli.com/TW/NewslettersDetail/7584.htm（理律：個資會籌備處 115.01.22 預告） | 「個人資料事故通知通報及應變辦法」草案 | 相符（verified_by 不是官方來源） |
| 個人資料檔案安全維護管理辦法（草案） | 同上；https://www.mlses.mlc.edu.tw/wp-content/uploads/sites/55/2026/02/個人資料保護委員會籌備處公告-1.pdf | 「個人資料檔案安全維護管理辦法草案」 | 相符（verified_by 不是官方來源） |
| 資通安全管理法 | https://laws.gov.taipei/law/LawSearch/LawExport/FL088622?type=0 | 資通安全管理法；修正 民國 114 年 09 月 24 日 | 相符 |
| 資通安全管理法施行細則 | https://laws.gov.taipei/law/LawSearch/LawExport/FL089964?type=0 | 修正 民國 115 年 01 月 05 日 | 相符 |
| 資通安全事件通報應變及演練辦法 | https://laws.gov.taipei/law/LawSearch/LawExport/FL089967?type=0 ；https://laws.gov.taipei/Law/LawSearch/LawRelateLaw/FL089967 | 115.01.05 由「資通安全事件通報及應變辦法」更名 | 相符（note 欄的更名說明也正確） |
| 資通安全情資分享辦法 | https://www-api.moda.gov.tw/File/Get/acs/zh-tw/yY9Q49eChtamqeu（資安署 115 年 1 月月報） | 《資通安全情資分享辦法》 | 相符（verified_by 是國小網站 PDF，不是官方來源） |
| 資通安全維護計畫實施情形稽核辦法 | https://www-api.moda.gov.tw/File/Get/moda/zh-tw/jYHVlGUY3O9Kgx3 ；https://isec.nfu.edu.tw/…/資通安全維護計畫實施情形稽核辦法.pdf | 「資通安全維護計畫實施情形稽核辦法」 | 相符 |
| 數位發展部所管特定非公務機關資通安全管理作業辦法 | https://www-api.moda.gov.tw/File/Get/moda/zh-tw/jYHVlGUY3O9Kgx3 | 數位部修正草案總說明中的現行名稱 | 相符（verified_by 是修正草案說明，不是法規頁） |
| 資通安全責任等級分級辦法 | https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=A0030304 | 修正 民國 115 年 01 月 07 日 | 相符 |
| 公務機關所屬人員辦理資通安全事項作業辦法 | https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=A0030308 | 修正 民國 115 年 01 月 13 日 | 相符 |
| 危害國家資通安全產品審查辦法 | https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=K0060168 | 發布 民國 114 年 12 月 19 日 | 相符 |
| 電信事業資通安全管理辦法 | https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=K0060140 | 發布 民國 109 年 07 月 09 日 | 相符 |
| 數位經濟相關產業個人資料檔案安全維護管理辦法 | https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=K0010162 | 發布 民國 112 年 10 月 12 日 | 相符 |
| 經濟部所管特定非公務機關資通安全管理作業辦法 | pcode=J0010055 兩次回傳 MOJ「Error」頁；改查 https://6laws.net/6law/law3/經濟部所管特定非公務機關資通安全管理作業辦法.htm（非官方），WebSearch 結果另有 law.moj.gov.tw 與 moda.gov.tw 頁面 | 115.11.06 經資字第11500745550號令修正發布 | 名稱相符；pcode 沒有從官方頁直接確認 |
| 製造業及技術服務業個人資料檔案安全維護管理辦法 | pcode=J0030130 | 修正 民國 112 年 10 月 30 日 | 相符 |
| 零售業個人資料檔案安全維護管理辦法 | pcode=J0080062 | 修正 民國 113 年 11 月 13 日 | 相符 |
| 電業與公用天然氣事業及加油站業個人資料檔案安全維護管理辦法 | pcode=J0130066 | 修正 民國 112 年 11 月 03 日 | 相符 |
| 自來水事業個人資料檔案安全維護計畫標準辦法 | pcode=J0110089 | 修正 民國 111 年 03 月 14 日 | 相符 |
| 著作權集體管理團體個人資料檔案安全維護管理辦法 | pcode=J0070050 | 發布 民國 110 年 12 月 21 日 | 相符 |
| 國家科學及技術委員會所管特定非公務機關資通安全管理作業辦法 | pcode=H0160073 | 修正 民國 111 年 12 月 01 日 | 相符 |
| 公開發行公司建立內部控制制度處理準則 | https://law.fsc.gov.tw/LawContent.aspx?id=FL021141 | 修正 民國 113 年 04 月 22 日 | 相符 |
| 證券暨期貨市場各服務事業建立內部控制制度處理準則 | https://law.fsc.gov.tw/LawContent.aspx?id=FL022569 | 修正 民國 113 年 04 月 22 日 | 相符 |
| 金融監督管理委員會指定非公務機關個人資料檔案安全維護辦法 | https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=G0380233 | 修正 民國 110 年 12 月 14 日；第 1 條「本辦法依個人資料保護法（以下簡稱本法）第二十七條第三項規定訂定之。」 | 相符 |
| 上市上櫃公司資通安全管控指引（twse／tpex） | https://twse-regulation.twse.com.tw/TW/law/DAT0201.aspx?FLCODE=FL104387 | 修正 民國 115 年 11 月 09 日 | 相符（tpex 條目的 verified_by 是新聞） |
| 臺灣證券交易所股份有限公司對有價證券上市公司重大訊息之查證暨公開處理程序 | https://twse-regulation.twse.com.tw/TW/law/DAT0201.aspx?FLCODE=FL007111 | 用「臺」；修正 民國 115 年 09 月 30 日 | 相符 |
| 財團法人中華民國證券櫃檯買賣中心對有價證券上櫃公司重大訊息之查證暨公開處理程序 | https://www.selaw.com.tw/Chinese/RegulatoryInformationResult?sysNumber=LW10812093 | 用「櫃檯」；115.9.30 | 相符 |
| 金融控股公司及銀行業內部控制及稽核制度實施辦法 | https://law.fsc.gov.tw/LawContent.aspx?id=FL049894 | 修正 民國 115 年 05 月 06 日 | 相符 |
| 金融監督管理委員會所管特定非公務機關資通安全管理作業辦法 | https://law.fsc.gov.tw/LawContent.aspx?id=GL002646 | 108.02.27；第 1 條與 seed note 相符 | 相符 |
| 金融業導入零信任架構參考指引 | https://law.fsc.gov.tw/LawContent.aspx?id=GL003923 | 113.07.15；金管資字第1130193942號函 | 相符 |
| 金融業後量子密碼遷移參考指引 | https://law.fsc.gov.tw/LawCategoryContentList.aspx?CategoryID=A104&size=40&page=1 | 115.06.17 | 相符 |

### 1b. 國際條目（5 筆）

| seed 名稱 | 查證 | 結果 |
|---|---|---|
| `Regulation (EU) 2016/679 (General Data Protection Regulation)` | 正式標題為「Regulation (EU) 2016/679 of the European Parliament and of the Council of 27 April 2016 on the protection of natural persons with regard to the processing of personal data and on the free movement of such data, and repealing Directive 95/46/EC (General Data Protection Regulation)」 | **存疑**：這是自行縮寫的引用形式，不是官方標題的逐字文字 |
| `Digital Omnibus Regulation Proposal COM(2025) 837 final` | 歐洲議會 Legislative Train 頁的標題是「Digital Omnibus Regulation Proposal」，文件編號是「COM(2025) 837 final」，程序 2025/0360(COD) 的名稱是「Simplification of the digital legislative framework (Digital Omnibus)」 | **存疑**：名稱由兩個官方字串拼接而成，不是單一官方標題 |
| ISO/IEC 27001:2022（含 Amd 1:2024） | 標準編號正確 | 相符 |
| ISO/IEC 27701:2025（iso_id 85819） | WebSearch 有 https://www.iso.org/standard/85819.html 等結果（iso.org 本身無法 WebFetch） | 相符 |
| BS 10012:2017+A1:2018 | https://knowledge.bsigroup.com/products/data-protection-specification-for-a-personal-information-management-system：Withdrawn，24 Apr 2026 | 相符（note 欄也正確） |

### 1c. verified_by 不是官方或主管機關來源的條目（config/seed_laws.yaml）

| 條目 | 目前的 verified_by | 問題 |
|---|---|---|
| 個人資料保護法施行細則 | isms.thu.edu.tw 的大學 PDF | 非官方；應改為 law.moj.gov.tw pcode=I0050022 |
| 個人資料事故通知通報及應變辦法 | www.cio.com.tw/108736/（科技媒體） | 非官方；應改為個資會籌備處或 join.gov.tw 預告頁 |
| 個人資料檔案安全維護管理辦法 | mlses.mlc.edu.tw 的國小網站 PDF | 非官方（轉載的公文） |
| 資通安全情資分享辦法 | esweb.wgps.tp.edu.tw 的國小網站 PDF | 非官方 |
| 上市上櫃公司資通安全管控指引（tpex） | wantrich.chinatimes.com（新聞） | 非官方；應改為 TWSE FL104387 或櫃買中心頁 |
| ISO/IEC 27701:2025 | dnv.com（驗證機構） | 可信但非官方；`url` 欄已是 iso.org |
| 證券暨期貨市場各服務事業建立內部控制制度處理準則 | dsp.twse.com.tw 的另一份指引 PDF | 間接佐證；應改為 law.fsc.gov.tw FL022569 |

另外，config/seed_laws.yaml 檔頭第 2 行要求 verified_by「必須是實際查證過的 URL」，但沒有要求必須是官方來源；這與本次查核要求的「官方或可信來源」之間有落差。

---

## 2. 原文保留 — 通過（附觀察）

- 正規化函式 `regcrawler/normalize.py:70-73 norm_text`、`:76-84 content_hash`、`:87-88 date_tuple`、`:51-67 article_key` 只用在雜湊和比對鍵：
  - regcrawler/cli.py:158、172 只用 content_hash 和 date_tuple 判斷變動；寫進 Excel 的 `法規名稱`、`公布日` 等欄位都直接取 `doc.*` 原值（cli.py:135-141）。
  - regcrawler/legal_ast/diff.py:38、48、69、255、302、308 只在比對時使用 norm_text；ArticleDiff.old_text／new_text 都是原文；codegen.render_inline（legal_ast/codegen.py:86-88）以原文逐字比對。
  - sources/fsc.py:440-443、528、575；twse.py:62-68、210；tpex.py:154 只用 norm_text 比對名稱，輸出仍用 entry.name 或頁面原文。
  - excel_crawl.py:35-43、excel_diff.py:58-69 只移除 openpyxl 不接受的控制字元，並截斷超過 32767 字元的儲存格，屬於合理範圍。
  - store.py:41-53 safe_name 只用 NFC 處理檔名，快照 JSON 內的文件內容是原文（store.py:66-77）。
- 範例 Excel（/home/claude/sample/範例_AST條文差異比對_20261127.xlsx）的條文欄和官方原文一致，包括全形括號。

輕微觀察（不屬於 norm_text／NFKC 外洩，建議改善）：
1. regcrawler/sources/twse.py:179：`effect_clause(re.sub(r"\s+", "", …))` 會刪除沿革中的所有空白，再寫入 `施行日` 欄。這屬於「去空白」後輸出，雖然出現在日期欄而不是條文或名稱欄。
2. regcrawler/sources/moj.py:946：函釋標題用 `re.sub(r"\s+", " ")` 壓縮空白，而這個標題在 moj.py:1045 會成為 LawDocument.name，也就是 Excel 的法規名稱欄。
3. regcrawler/sources/intl.py:89、95-96 `_clean`：把 NBSP、U+2000–U+200B、全形空白等壓縮成一個半形空白，並移除 EUR-Lex 編輯標記 ▼B／►M；結果會寫入 GDPR 條文。
4. regcrawler/sources/moj.py:379、383、402、663、677：日期和條號的空白會被壓縮。
5. regcrawler/sources/moj.py:221-223：施行日的「施行日期，由X定之」會改寫為「未定（由X定之）」，「，第」會改寫為「；第」。SPEC §2 有舉出這個寫法，但仍屬改寫。另外 moj.py:639-647 和 moda.py:168-177 會用啟發式方式接回排版斷行，可能把兩項誤併成一項；這只影響基準舊版條文。

---

## 3. 反爬註記 — 不通過

### 3a. regcrawler/fetch/chain.py：通過
- chain.py:228-249：只有在 `method_hint="official_api"` 時才先嘗試 official_api（models.py:8-10 也說明 official_api 不計入 5 種方法）；之後依 `FETCH_METHODS = ("requests","requests_alt","playwright","playwright_human","wayback")` 依序執行。5 種方法確實各不相同：
  - requests：chain.py:339-341
  - requests_alt：chain.py:343-368，使用不同 UA、`Accept-Encoding: identity`、Referer 設為首頁，並先取首頁 cookie
  - playwright：chain.py:370-371
  - playwright_human：chain.py:373-374、429-449，包含貝茲曲線滑鼠移動、捲動、1.5–6 秒停頓、隨機 viewport、Asia/Taipei
  - wayback：chain.py:451-468，使用 available API 和 `id_` 原始快照
- 全部失敗時，chain.py:249 會回傳 `note=ANTI_BOT_FAIL_NOTE`。
- 存疑：`wayback_ok=False` 時 wayback 會被略過，實際只試 4 種方法，但 chain.py:238-240、249 仍回傳「嘗試 5 種不同方法」。呼叫端在 sources/intl.py:401-402（SPARQL）。

### 3b. 各來源模組與 cli：不通過
用 FetchResult(ok=False, note=ANTI_BOT_FAIL_NOTE) 模擬每個網頁的 5 種方法都失敗，再執行 `regcrawler.cli.main()`。產出的 `法規爬蟲結果_*.xlsx` 共 38 列，**只有 13 列的備註完全等於規定字串，25 列不符**：

| 模組 | 結果 | 實際備註開頭（節錄） | 位置 |
|---|---|---|---|
| moj（19 筆全部） | 不符 | `官方開放資料無法取得（law: https://law.moj.gov.tw/api/ch/law/json：本次搜尋的網頁有 反爬蟲機制…），改抓網頁；本次搜尋的網頁有 反爬蟲機制…` | sources/moj.py:560-562 組出 pre_note，再由 :574-575 加在前面（:569 也有同樣問題） |
| moda（2 筆全部） | 不符 | `https://law.pdpc.gov.tw/DraftForum.aspx：本次搜尋的網頁有 反爬蟲機制…；…；列表頁未定位到，改用已查證之預告紀錄；預告頁 … 無法取得：…` | sources/moda.py:287、295、333、336、340、370 |
| intl GDPR | 不符 | `本次搜尋的網頁有 反爬蟲機制…；SPARQL 端點無法取得；consolidated 版本清單改用已查證清單…` | sources/intl.py:461（fetch_gdpr 在 _fail_doc 之後又 join notes） |
| intl Digital Omnibus | 不符 | `EUR-Lex procedure：本次搜尋的網頁有…；EP Legislative Observatory：本次搜尋的網頁有…` | sources/intl.py:533、546 |
| intl BS 10012 | 不符 | `本次搜尋的網頁有 反爬蟲機制…；無法取得 BSI 官方頁面，狀態以 NA 記錄（經銷商標示不採用）` | sources/intl.py:793-795（另外來源模組回傳的 doc.ok=True，要到 cli.py:148-149 才被清空 metadata） |
| fsc（7）、twse（2）、tpex（2）、intl ISO（2） | 完全相符 | — | fsc.py:257-262 fail_note；tpex.py:135 |

另外，regcrawler/cli.py:152-154 只在 `備註` 為空時才補上 ANTI_BOT_FAIL_NOTE，沒有把含診斷前後綴的備註收斂成規定字串。excel_crawl.py:85、102、121 的淡紅底色與「反爬失敗數」統計是用「完全等於」判斷，所以這 25 列在執行摘要中會被算成「其他失敗」，底色也不會標紅。

現有測試沒有涵蓋「所有網頁都失敗」時 moj、moda、intl 的端到端情境，所以 209 個測試全數通過，卻沒有發現這個問題。

---

## 4. Excel 欄位 — 通過

- `cd /home/claude/regcrawler && python -m pytest -q` → **209 passed in 1.25s**。
- subtask1：regcrawler/excel_crawl.py:22-23 的 COLUMNS = 法規名稱、法規狀態、公布日、修正日、施行日、本次新增、本次刪除、本次變動、資料來源、取得方式、SHA-512、備註，和 SPEC §7 一致。/home/claude/sample/範例_法規爬蟲結果_20261127.xlsx 的工作表「法規爬蟲結果」表頭一致，另有「執行摘要」工作表。
- subtask2：regcrawler/excel_diff.py:29-31 有 10 欄，包含「本次 AST 判斷新舊條文差異的依據及 rationale」。工作表「連動影響」（excel_diff.py:26、31）的表頭為 受影響法規｜受影響條文｜所依據之母法條文｜母法異動類型｜說明。範例 /home/claude/sample/範例_AST條文差異比對_20261127.xlsx 有「條文差異」（27 列資料）、「連動影響」（1 列：金管會安維辦法第 1 條 ← 個資法第二十七條第三項 刪除條文）、「圖例與方法說明」三個工作表。
- 範例的 PDPA 列：本次新增 9 條、本次刪除第 27 條、本次變動 16 條，和 SPEC §8 官方沿革完全一致；第 48 條判為「修正條文；罰則加重」。

---

## 5. Fixture 來源與逐字比對 — 通過（附存疑）

- 全部 48 個 fixture 的檔頭（HTML 註解、`#` 行或 JSON `_source`／`_fixture_source`）都有來源 URL，並宣稱內容取自官方或指定來源，例如 tests/fixtures/rules/pdpa_114.txt:1-8、tests/fixtures/legal_ast/README.txt。
- 抽查與官方網頁逐字比對（以 Python 字串完全相等判斷）：

| 抽查段落 | Fixture | 官方來源 | 結果 |
|---|---|---|---|
| 個資法第 12 條（4 項 2 款） | tests/fixtures/rules/pdpa_114.txt:88-94；tests/fixtures/legal_ast/pdpa_114.json | https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=I0050021 | 逐字相符，0 個不一致字元 |
| 個資法第 20-1 條（2 項） | pdpa_114.txt:150-152；pdpa_114.json | 同上 | 逐字相符 |
| 個資法第 48 條（114 版，4 項 8 款） | pdpa_114.txt:283-295；pdpa_114.json | 同上 | 逐字相符 |
| 個資法第 48 條（112 版，3 項 3 款） | tests/fixtures/legal_ast/pdpa_112.json；rules/pdpa_112.txt:244-250 | https://ws.ndc.gov.tw/001/administrator/30/laws/4/1e71c611-d58c-4ff4-8d1e-405987f5a5e4.pdf（檔頭「112 年 5 月 31 日總統華總一經字第 11200045441 號令修正公布」） | 逐字相符 |
| 金管會安維辦法第 1 條 | tests/fixtures/rules/fsc_pdpa_security_rules.txt:9 | pcode=G0380233 | 逐字相符，包含全形括號 |

  註：WebFetch 回傳的款文字省略了款首編號「一、」「二、」，比對時依官方版面補上。

- 存疑觀察：
  1. 多個 HTML fixture 的標記結構是重建的，檔頭有誠實揭露，例如 fsc/FL007032_LawContent.html、moj/LawOldVer_I0050021.html、intl/sparql_consolidated_32016R0679.json、moda/draft_incident_rows.json。這表示解析器測試無法證明程式能處理真實頁面的 DOM，首次在 Actions 上執行時，需人工確認解析結果。
  2. moj/ChLaw_subset.json、ChOrder_subset.json 取自第三方 GitHub 鏡像 kong0107/mojLawSplitJSON，不是官方 API 的直接下載。
  3. rules/pdpa_112.txt 取自壽險公會 law.lia-roc.org.tw（業界公會，非政府機關），不過第 48 條已和國發會 PDF 交叉比對一致。
  4. tests/test_rules_linkage.py:48、tests/test_excel_diff.py:42 使用半形括號「(以下簡稱本法)」，和官方全形「（以下簡稱本法）」不同；這是單元測試的自編字串，沒有宣稱官方來源，影響輕微。

---

## 6. 安全與合規 — 通過（附存疑）

- CAPTCHA 破解：沒有。chain.py:73-75 只用來**偵測** reCAPTCHA、hCaptcha 和驗證碼頁，偵測到即判定該方法失敗（chain.py:144-165），沒有任何解題程式碼。
- 代理池：沒有。全專案 grep `proxies|proxy` 只在 fixture 檔頭說明文字中出現。
- 繞過登入：沒有。grep `login|password|Authorization|Bearer` 沒有出現在程式碼中，只在測試字串出現。
- 關閉 TLS 驗證：沒有 `verify=False` 或 `CERT_NONE`。chain.py:101-117 只清除 Python 3.13 的 `VERIFY_X509_STRICT` 旗標，仍保留憑證鏈驗證和主機名稱驗證；理由已在註解說明（law.moj.gov.tw 憑證缺 SKI），屬於可接受的放寬。
- 密鑰或憑證：沒有寫在程式內。workflow 只使用內建的 `GITHUB_TOKEN`，透過 checkout 取得。
- 存疑：chain.py:279-300 的 `_crawl_delay` 只套用 robots.txt 的 Crawl-delay，沒有檢查 `can_fetch`／Disallow。sources/moj.py:599-600 的註解自己寫明「官方 LawOldVer／LawHistory 在 robots.txt 被 Disallow……GitHub Actions 上仍會先試官方頁」，而 moj.py:492 每次都會請求 LAWHISTORY_URL。查核時 WebFetch 讀取 https://law.moj.gov.tw/robots.txt 也回報 robots_disallowed，無法取得原文。對上市公司稽核用途而言，這是合規風險，建議遵守 Disallow。

---

## 7. GitHub Actions — 通過

- .github/workflows/regcrawl.yml:18 `cron: "17 1 1,15 * *"`，即每月 1 日與 15 日 01:17 UTC（台北時間 09:17，同一天）。
- :19-30 有 `workflow_dispatch`，可輸入 sources 與 force_baseline。
- :89-96 使用 `actions/upload-artifact@v4` 上傳 `output/*.xlsx`，並設 `if: always()`、保留 90 天。
- :32-33 `permissions: contents: write`，這是把 snapshots commit 回 repo（:98-114）所需的最小權限，沒有其他權限。
- 建議（非必要）：actions 可以釘選 commit SHA；`SOURCES` 已透過 env 傳入，沒有 script injection 風險。

---

## 必須修正

1. **反爬註記完全一致（#3）**
   - regcrawler/sources/moj.py:557-576 `_fetch_one`：若最後的 HTML 擷取 `res.note == ANTI_BOT_FAIL_NOTE` 且 `not doc.ok`，就令 `doc.fetch_note = ANTI_BOT_FAIL_NOTE`，不要在前面加 pre_note；開放資料失敗的細節改寫 log。:566-570（pcode 定位失敗）也一樣：若所有搜尋頁都因反爬失敗，備註只放規定字串。
   - regcrawler/sources/moj.py:577-592 `fetch`：主文件已是反爬失敗時，不要再用 `_add_note` 附加函釋的說明。
   - regcrawler/sources/moda.py:358-375 `fetch`：若最後 `not doc.ok`，而且列表頁、預告頁、附件全部是 ANTI_BOT_FAIL_NOTE，就令 `doc.fetch_note = ANTI_BOT_FAIL_NOTE`。
   - regcrawler/sources/intl.py:452-461 `fetch_gdpr`：`doc` 是失敗文件時不要再 join notes；:546 改照 :684 的寫法，所有 note 都含反爬字串時只回傳 ANTI_BOT_FAIL_NOTE；:793-795 `fetch_bsi` 失敗時備註只用 `res.note`，「狀態以 NA 記錄」改放 metadata 或 log。
   - 保險措施：regcrawler/cli.py:152-154 改為「`not doc.ok` 且 `ANTI_BOT_FAIL_NOTE in doc.fetch_note` 且沒有其他非反爬錯誤」時，令 `row["備註"] = ANTI_BOT_FAIL_NOTE`。
   - 新增測試 tests/test_integration_cli.py：使用全部回傳 `FetchResult(ok=False, note=ANTI_BOT_FAIL_NOTE)` 的假 Fetcher 跑 `cli.main`，斷言每一列 `備註 == ANTI_BOT_FAIL_NOTE`。
2. **wayback 被略過時的註記（#3 存疑）**：regcrawler/fetch/chain.py:249，若有任何方法因 `wayback_ok=False` 被略過，就不要回傳 ANTI_BOT_FAIL_NOTE（因為實際只試了 4 種），改回傳「嘗試 4 種方法（wayback 不適用）仍無法取得資料」之類的說明；或者讓呼叫端 intl.py:401-402 改回 `wayback_ok=True`。
3. **seed verified_by 改為官方來源（#1）**：config/seed_laws.yaml
   - 個人資料保護法施行細則 → `https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=I0050022`
   - 證券暨期貨市場各服務事業建立內部控制制度處理準則 → `https://law.fsc.gov.tw/LawContent.aspx?id=FL022569`
   - 公開發行公司建立內部控制制度處理準則 → `https://law.fsc.gov.tw/LawContent.aspx?id=FL021141`
   - 上市上櫃公司資通安全管控指引（tpex）→ `https://twse-regulation.twse.com.tw/TW/law/DAT0201.aspx?FLCODE=FL104387`
   - 個人資料事故通知通報及應變辦法、個人資料檔案安全維護管理辦法 → 改為個資會籌備處（law.pdpc.gov.tw）或 join.gov.tw 的官方預告頁
   - 資通安全情資分享辦法 → 改為全國法規資料庫或臺北市法規查詢系統的頁面
   - 並在檔頭第 2 行規則加上「verified_by 應為官方或主管機關頁面」。
4. **國際條目名稱（#1）**：config/seed_laws.yaml 的 GDPR 與 Digital Omnibus 兩筆，若 `name` 要維持「官方正式名稱原文」，請改用 EUR-Lex 上的完整官方標題，或在 SPEC 明定國際條目可使用官方簡稱，並在 note 註明完整標題。

建議修正（非阻斷）：
- regcrawler/sources/twse.py:179：先從原文取出施行條款，不要先刪除空白（#2 觀察 1）。
- regcrawler/sources/moj.py:946：保留函釋標題原文（#2 觀察 2）。
- regcrawler/fetch/chain.py:279-300：加入 robots.txt `can_fetch` 檢查；regcrawler/sources/moj.py:492 改為不請求被 Disallow 的 LawHistory 頁（#6）。

---

## 主控修正紀錄（2026-11-27，獨立驗證後）

| 驗證項目 | 處理 |
|---|---|
| #3 反爬註記不完全一致 | `cli.py`：擷取失敗且備註含規定字串時，備註一律改為「完全等於」規定字串，原細節寫入執行紀錄（log）。新增端到端測試 `test_all_blocked_note_is_exact`：以真實種子清單與真實來源模組、所有請求皆被擋的情境執行，38 列備註全部完全相符。 |
| #3 wayback 略過仍宣稱 5 種 | `fetch/chain.py`：實際嘗試未滿 5 種時，改註記「嘗試 N 種方法（Wayback 不適用於此類請求）仍無法取得資料」。測試已更新。 |
| #1 verified_by 非官方 | 7 筆改為官方或主管機關頁面（全國法規資料庫、金管會、證交所、join.gov.tw、資安署、ISO）。種子清單檔頭規則已更新。 |
| #1 國際條目名稱 | 維持「官方編號＋官方簡稱」，於 note 註明完整官方標題以 EUR-Lex 為準。 |
| #6 robots.txt Disallow | 依使用者「用盡一切方法」指示維持現行做法，已在 README 明確揭露，並說明如何改為遵守。 |
| 經濟部所管特定非公務機關資通安全管理作業辦法 | 名稱來自法務部官方開放資料；首次在 GitHub Actions 執行時，moj 模組會以官方開放資料逐字比對名稱，不符時會在備註註明查無。 |

修正後 `python -m pytest -q`：210 passed。
