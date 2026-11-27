# 資安與個資法規知識庫全自動爬蟲 + AST-level Legal Diff

給上市公司總稽核、法遵部門、資安長使用的法規異動監測工具：

- **subtask1｜多來源權威法規爬蟲**：每月 1 日、15 日（台灣時間 09:17）自動執行，也可隨時手動執行。產出 `法規爬蟲結果_YYYYMMDD.xlsx`。
- **subtask2｜AST 條文差異比對**：偵測到法規的公布日、修正日、施行日或內文 SHA-512 有變動時，自動逐字比對新舊條文，標出「新增條文」「刪除條文」「修正條文」「罰則加重」「通知時限縮短」「通報時限縮短」與「連動影響」。產出 `AST條文差異比對_YYYYMMDD.xlsx`。

完全在 GitHub Actions 免費方案執行（public repo 不限分鐘數），不需要自己的伺服器。

---

## 一、部署步驟（約 10 分鐘）

1. **建立 repo**：在 GitHub 建立一個新的 **public** repository（不要勾選初始化 README）。
2. **上傳程式**：把 zip 解壓縮後的所有檔案上傳到 repo 根目錄。注意 `.github/` 是隱藏資料夾，網頁拖曳上傳可能漏掉，建議用 git：
   ```bash
   cd regcrawler
   git init && git add . && git commit -m "init"
   git branch -M main
   git remote add origin https://github.com/<你的帳號>/<repo名稱>.git
   git push -u origin main
   ```
3. **開啟寫入權限**（爬蟲要把版本快照 commit 回 repo）：
   repo → **Settings → Actions → General → Workflow permissions** → 選 **Read and write permissions** → Save。
4. **第一次手動執行**：repo → **Actions** → 左側 **regcrawl** → **Run workflow**。
   第一次執行會依「法規沿革」抓上一版作為比對基準，所以第一次就會有差異比對結果。
5. **下載 Excel**：執行完成後，點進該次執行，頁面底部 **Artifacts** 有 `regcrawl-YYYYMMDD-HHMM`，下載後解壓縮即得兩份 Excel（保留 90 天；長期版本紀錄在 repo 的 `snapshots/`）。

### 手動執行的選項
- `sources`：只跑指定來源，逗號分隔，例如 `moj,fsc`。可用值：`moj`（法務部）、`moda`（數位部／個資會草案）、`fsc`（金管會）、`twse`（證交所）、`tpex`（櫃買中心）、`intl`（國際）。
- `force_baseline`：忽略既有快照，重新以法規沿革前一版為基準。

### 本機執行（選用）
```bash
pip install -r requirements.txt
python -m playwright install --with-deps chromium
python -m regcrawler.cli --out output            # 全部來源
python -m regcrawler.cli --out output --sources moj
python -m pytest -q                               # 離線測試
```

---

## 二、資料來源與擷取方式

| 來源 | 擷取方式 |
|---|---|
| 法務部全國法規資料庫 | **官方 Open API**（`law.moj.gov.tw/api/ch/law/json`、`/api/ch/order/json`），失敗才爬網頁；舊版本取自歷史法規頁 |
| 數位發展部資通安全署／個人資料保護委員會籌備處 | 草案預告頁＋行政院公報 PDF（解析條文對照表） |
| 金管會主管法規查詢系統 | 網頁（無 Open API）；含函令 |
| 證交所法規分享知識庫 | 網頁（證交所 OpenAPI 無法規內文端點） |
| 櫃買中心規章 | 證券暨期貨法令判解查詢系統（櫃買中心官網指引之查詢系統） |
| GDPR | EUR-Lex 官方 CELLAR SPARQL＋consolidated 全文 |
| ISO/IEC 27001:2022、ISO/IEC 27701:2025、BS 10012 | **只追蹤後設資料**（版次、狀態、增修、撤銷）。標準全文為付費著作權文件，不爬取 |
| 函釋 | 金管會函令取自金管會系統；個資法、資安法函釋取自臺北市法規查詢系統轉載的中央函釋（主管機關官網無可用列表頁），收錄最近 3 年 |

### 反爬蟲對抗（每個網頁依序嘗試 5 種方法）
有官方 Open API 時先用官方 API，失敗或沒有 API 時依序嘗試以下 5 種：
1. `requests`：一般連線，含重試、指數退避、每網域間隔 ≥ 2 秒、遵守 Crawl-delay
2. `requests_alt`：換 User-Agent、HTTP/1.1 標頭、先取首頁 cookie、帶 Referer
3. `playwright`：無頭瀏覽器，可處理 JavaScript 載入的頁面
4. `playwright_human`：模擬人為操作，包括隨機滑鼠軌跡、分段捲動、1.5～6 秒停頓、台灣時區與語系
5. `wayback`：Internet Archive Wayback Machine 快照。採用時「資料來源」欄會標註快照時間

5 種全部失敗時，備註欄標註：`本次搜尋的網頁有 反爬蟲機制，嘗試 5 種不同方法之後仍無法取得資料`。
本程式不做 CAPTCHA 破解、不用代理池規避封鎖、不繞過登入，也不關閉 TLS 憑證驗證。
依你的指示「用盡一切方法」，程式只遵守 robots.txt 的 Crawl-delay（請求間隔），不遵守 Disallow（例如全國法規資料庫的歷史版本頁在 robots.txt 中被 Disallow，程式仍會嘗試抓取，失敗才改用鏡像）。如需改為遵守 Disallow，可在 regcrawler/fetch/chain.py 加入 can_fetch 檢查。

---

## 三、Excel 說明

### 法規爬蟲結果（subtask1）
| 欄位 | 說明 |
|---|---|
| 法規名稱 | 原文照錄 |
| 法規狀態 | 現行／草案／提案 |
| 公布日、修正日、施行日 | 原文照錄。官方寫明未定者照錄（例如「未定（由行政院定之）」）；無資料填 NA |
| 本次新增／本次刪除／本次變動 | 條號清單 |
| 資料來源 | 實際取得資料的網址（Wayback 快照會註明時間） |
| 取得方式 | 實際成功的方法 |
| SHA-512 | 正規化條文內容的雜湊值（正規化只用於雜湊，輸出一律原文） |
| 備註 | 反爬註記、首次基準來源、非官方鏡像註記、連動影響、標準撤銷狀態等 |

另有「執行摘要」工作表，列出執行時間、成功數、失敗數、各來源統計。

### AST 條文差異比對（subtask2）
- **條文差異**工作表：逐字差異欄以紅色刪除線標示刪除文字、綠色底線標示新增文字；依差異類型標上底色。
- **本次 AST 判斷新舊條文差異的依據及 rationale**：每列列出四個階段——Lexing（詞法分析：token 統計）、Parsing（語法分析：條→項→款→目樹與條號對齊）、Traversal（遍歷與轉換：節點層級的新增、刪除、修正、次序變更）、Generation（逐字差異產生）——以及語意規則判定，判定理由會引用原文數額或時限。
- **連動影響**工作表：子法所依據的母法條文被刪除或修正時列出。例如個資法第 27 條刪除 → 「金融監督管理委員會指定非公務機關個人資料檔案安全維護辦法」第 1 條的授權依據受影響。
- **圖例與方法說明**工作表。

---

## 四、維護

- **追蹤清單**：`config/seed_laws.yaml`。新增法規時，`name` 必須是官方正式名稱，並填寫 `verified_by`（查證網址）。
- **函釋範圍**：`regcrawler/sources/moj.py` 的 `INTERPRETATION_YEARS`（預設 3 年），以及 `regcrawler/sources/fsc.py` 的 `INTERP_KEYWORDS`。
- **排程停用**：public repo 60 天沒有活動時，GitHub 會停用排程。每次執行若有新快照會自動 commit，可維持活動；如收到停用通知，到 Actions 頁面重新 Enable 即可。
- **規格文件**：`SPEC.md`（主控統一規範）。

## 五、已知限制（第一次執行後請檢查）

1. 開發環境無法連線到各法規網站，網頁解析邏輯是依官方頁面的文字內容撰寫，再用離線樣本測試。第一次在 GitHub Actions 執行後，請查看 Excel 的「備註」與「執行摘要」，確認各來源都有抓到條文。若有來源解析失敗，請把 Actions 執行紀錄（log）提供給開發者調整。
2. 櫃買中心「重大訊息之查證暨公開處理程序」的全文由 JavaScript 載入，目前只追蹤後設資料與沿革；資安管控指引的條文取自證交所（兩者共同制定）。
3. 銀行公會自律規範（例如「金融機構辦理電子銀行業務安全控管作業基準」）不在金管會或全國法規資料庫系統內，銀行公會網站目前無法驗證可存取，因此尚未納入。
4. 首次基準線：全國法規資料庫的歷史版本頁若無法取得，會改用壽險公會或臺北市法規查詢系統的鏡像，並在備註註明「基準版本取自非官方鏡像」。
5. 語意規則（罰則加重、時限縮短）是依條文原文的數額與時限抽取判斷；條次大幅重組時，判斷理由會註明「建議人工覆核」。
