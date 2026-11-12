# Barbet Harness：Skill、記憶檢索、工具與有限次修正

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/cathy910207-hub/barbet-harness/blob/main/run.ipynb)

驗證「模型權重不夠強時，能否靠外部記憶、skill、工具結果與驗證回饋，更可靠地完成任務」。

> ⚠️ 目前的模型是 **DUMMY 假模型**，只用來確認流程正確。所有數字都不是 Barbet 的結果。
> 取得主線的 checkpoint、renderer、解碼設定之前，不要用其他模型代替並稱為 Barbet 結果。

## 快速開始

```bash
python verify.py                                               # 驗證資料：產生 candidates／rejected／manifest
python importers/tulu_import.py --n 23                         # 匯入 Tülu 規則題（需連網，建議在 Colab 執行）
python tests/test_rule_checks.py                               # 測試規則檢查器（每種規則都有通過／失敗案例）
python tests/test_tulu_parse.py                                # 測試 Tülu 題目解析器
python tests/test_verify.py                                    # 測試驗證器本身（應顯示 25/25 通過）
python tests/test_tulu_integration.py                          # 匯入 → 驗證 → A/B/C 整合測試
python tests/test_pipeline.py                                  # 檢查 A/B/C 流程（應顯示「全部檢查通過」）
python run_experiment.py --dummy-script tests/dummy_script.json # 用假模型劇本跑 A/B/C
```

本輪的實際結果、已知限制與下一步，請見 [report.md](report.md)。

## 交付檔案

| 檔案 | 內容 |
|---|---|
| `data/candidates.jsonl` | 通過檢查的候選資料 |
| `data/rejected.jsonl` | 被淘汰的資料與排除原因 |
| `data/manifest.json` | 資料來源、revision、授權、生成方法與檔案雜湊值 |
| `verify.py` | 可重複執行的自動驗證程式 |
| `tests/` | 驗證器的正確與錯誤案例（`test_verify.py`）、流程測試（`test_pipeline.py`） |
| `report.md` | 實際結果、已知限制與下一步計畫 |

## Tülu 規則執行題

`importers/tulu_import.py` 只收錄「每一條規則都能解析、也都能用程式檢查」的題目，其餘放進 `data/raw/tulu_rejected.jsonl` 並寫明原因：

- 標籤中有不支援的規則類型
- 解析不到參數，或解析出互相衝突的值
- 題目中有標籤沒列出的規則（資料集的標籤有時會漏）
- 逐句／逐段規則、特定語言、佔位符等目前無法驗證的要求

每題會產生一個 skill（`data/skills/tulu/`），把規則逐條列出給模型看；同一份規則也是評分與 C 組回饋的依據。資料集的參考回答**不作為標準答案**，只記錄它是否通過規則，用來判斷解析是否合理。

## 資料流程：原始資料不會被覆寫

```
data/raw/tasks_draft.jsonl   題目草稿（AI 起草，不直接採信）
data/raw/corrections.jsonl   修正紀錄（原值、新值、原因）
        │
        ▼  python verify.py：套用修正 → 逐題用程式獨立重算／重查 → 群組檢查
        │
        ├─ data/candidates.jsonl   通過（每筆附 revisions：修正前後的驗證結果）
        ├─ data/rejected.jsonl     淘汰（附 reject_stage 與 reject_reasons）
        └─ data/manifest.json      版本與雜湊
```

要修正答案時，**不要直接改草稿**，而是在 `corrections.jsonl` 新增一行：

```json
{"correction_id": "corr_004", "task_id": "doc_001", "path": "answer.answer_contains",
 "old": ["80 元"], "new": ["80元"], "reason": "修正原因", "author": "你的名字", "date": "2026-11-12"}
```

`old` 必須和目前的值完全相同，否則這筆修正不會套用，該題會被淘汰，避免盲目覆寫。

結果會存在 `runs/<執行時間>/`：

| 檔案 | 內容 |
|---|---|
| `manifest.json` | 本次設定與所有版本雜湊（題庫、文件、skill、工具 schema、mock DB、renderer、checkpoint） |
| `traces.jsonl` | 每題每組一行，含每一步的完整 prompt、模型輸出、工具執行結果、回饋訊息、token 與延遲 |
| `summary.md` / `summary.json` | 成功率、錯誤分類、成本、「修正後變對」與「修正後反而變錯」的案例 |

## 三組實驗

| 組別 | 內容 |
|---|---|
| A | Barbet 原生回答。只看題目（含題目指定的工具清單），工具不會被執行 |
| B | A ＋ 檢索到的文件（BM25 top-k）或題目指定的 skill |
| C | B ＋ 工具實際執行 ＋ 執行期驗證 ＋ 最多 2 次修正；另外提供 calculator |

三組共用同一份程式，只用開關控制，確保 checkpoint、renderer、解碼設定、題目、資料版本相同。

## 專案結構

```
config.json            所有實驗設定（模型、renderer、解碼、預算、檢索、修正次數）
run_experiment.py      執行入口
run.ipynb              Colab 版本
harness/
  runner.py            A/B/C 流程：檢索 → 組 prompt → 生成 → 執行／驗證 → 回饋 → 修正
  model.py             DummyModel／BarbetModel、token 計數
  renderer.py          prompt 格式（⚠️ 暫用 simple_v0_TEMP）
  protocol.py          解析模型輸出（<answer> / <tool_call> / <ask>）
  retriever.py         文件切段、BM25 檢索、skill 載入
  tools.py             模擬工具、schema 驗證
  verifier.py          執行期驗證（不讀標準答案，C 組回饋用）
  rule_checks.py       可用程式驗證的寫作規則（逗號、大小寫、字數、句數、段落、條列、關鍵字……）
  grader.py            評分（唯一讀標準答案的地方，結果不回饋給模型；⚠️ 內部評分，非主線正式評分）
  metrics.py           統計與報告
verify.py              資料驗證（產生 candidates／rejected／manifest）
importers/
  tulu_import.py       Tülu 3 指令遵循資料匯入（固定 revision、固定種子、解析規則參數）
report.md              交付報告
data/
  raw/                 原始草稿與修正紀錄（不覆寫）；Tülu 匯入結果 tulu_draft／tulu_rejected／tulu_import_log
  candidates.jsonl  rejected.jsonl  manifest.json
  docs/  skills/（skills/tulu/ 為匯入產生）  tools/
tests/
  test_rule_checks.py  規則檢查器測試
  test_tulu_parse.py   Tülu 解析器測試
  test_tulu_integration.py  Tülu 整合測試（fixtures/ 為仿造資料）
  test_verify.py       驗證器測試（正確與錯誤案例）
  test_pipeline.py     流程檢查
  dummy_script.json    假模型劇本
```

## 防止標準答案洩漏的設計

1. `runner.public_view()` 只保留 `id`、`task_family`、`question`、`context` 四個欄位，`answer`、`verification`、`constraints`、`revisions` 都不會進到 prompt。
2. `verifier.py` 只用 schema、工具結果、文件庫段落清單、skill 輸出格式產生回饋，不 import 評分器。
3. `grader.py` 的評分結果只寫進 trace（欄位 `attempt_grades_for_analysis_only`），從不放回 prompt。
4. `tests/test_pipeline.py` 會檢查每一個 prompt 都不含標準答案。

## 資料格式（candidates.jsonl，一行一筆）

```json
{"id": "tool_001", "task_family": "tool_selection", "group_id": "grp_tool_fx_usd_twd",
 "source": {"type": "handwritten", "created_by": "Claude（AI 草稿）", "reviewed_by": null,
            "license": "本專案原創", "generation_method": "..."},
 "question": "幫我把 100 美元換成台幣。",
 "context": {"tools": ["convert_currency", "get_order_status"]},
 "answer": {"expect": "tool", "tool": "convert_currency",
            "args": {"amount": 100, "from_currency": "USD", "to_currency": "TWD"},
            "result": {"converted_amount": 3200.0}},
 "constraints": ["必須選擇正確工具", "參數值與型別必須符合工具 schema"],
 "verification": {"method": "execute_tool", "params": {}, "result": {"passed": true, "verifier": "verify_v0.2"}},
 "split": "unassigned", "tags": ["normal"], "revisions": []}
```

| 欄位 | 說明 |
|---|---|
| `task_family` | `doc_retrieval`／`rule_following`／`tool_selection`／`interpret_fix` |
| `group_id` | 同一題的原題、翻譯、改寫、反事實對照（例如換 skill）必須相同 |
| `context` | 給模型的情境：`tools`（可用工具）、`skill_id`（適用 skill） |
| `verification` | `verify.py` 用哪種方法獨立驗證答案（方法列表見 report.md） |
| `split` | 一律 `unassigned`，由主線統一切分 |

`answer.expect` 可用的值：

| expect | 用途 | 需要的欄位 |
|---|---|---|
| `answer` | 一般回答（文件題、規則題） | 文件題：`answer_contains`、`sources`；規則題：`answer_exact` |
| `not_found` | 文件中找不到，不可編造 | `forbidden_claims` |
| `contradiction` | 文件矛盾，需指出 | `sources`（所有矛盾來源）、`values`（各來源的說法） |
| `tool` | 需呼叫正確工具 | `tool`、`args`，可加 `result`、`final_answer_contains` |
| `no_tool` | 沒有適用工具 | — |
| `ask` | 缺必要參數，應先詢問 | `missing`、`ask_keywords` |
| `report_failure` | 工具失敗，不可宣稱完成 | `tool`、`args`、`failure_keywords`、`forbidden_claims` |

## 錯誤分類

| 類型 | 意思 |
|---|---|
| `fabricated_source` | 引用不存在的來源，或找不到答案卻附上來源 |
| `hallucinated_answer` | 文件中沒有，卻給出答案 |
| `missed_contradiction` | 文件矛盾但沒有指出 |
| `wrong_source` | 答案對但來源不完整 |
| `wrong_answer` | 答案錯誤 |
| `hallucinated_tool` | 呼叫不存在的工具 |
| `wrong_tool` / `unnecessary_tool` | 選錯工具／不需要卻呼叫工具 |
| `wrong_type` / `wrong_args` | 參數型別錯誤／參數值錯誤 |
| `missing_param_not_asked` | 缺參數卻沒有詢問，直接亂填 |
| `claimed_success_on_failure` | 工具失敗卻宣稱完成 |
| `wrong_result` / `wrong_final_answer` | 最終結果錯誤 |
| `no_answer` / `no_tool_call` / `no_ask` | 沒有產生需要的輸出 |
| `budget_exceeded` | 超過 token 預算 |

## 必測情境如何實驗

| 情境 | 做法 |
|---|---|
| 找不到文件 | `expect: not_found` 的題目 |
| 文件矛盾 | `expect: contradiction` 的題目 |
| 記憶位置 | config `retrieval.position` 設為 `start`／`middle`／`end`，並設 `num_distractors` |
| 無關段落 | config `retrieval.num_distractors` 設為 > 0 |
| skill 替換 | 同一個 `question`、不同 `context.skill_id` 與不同 `answer`（如 rule_001／rule_002），並使用**同一個 `group_id`** |
| 無適用工具 | `expect: no_tool` |
| 缺參數 | `expect: ask` |
| 工具失敗 | `expect: report_failure`，並在 `mock_db.json` 的 `failing_orders` 設定失敗情境 |

## 接上 Barbet 之前要向主線確認

- [ ] checkpoint 路徑與版本 → `config.json` 的 `model.checkpoint`、`model.revision`，並把 `model.type` 改為 `barbet`
- [ ] 是 base 模型還是有指令微調 → 若是 base，可能需要在 `renderer.few_shot` 加範例
- [ ] renderer（prompt 格式、特殊 token、工具呼叫格式）→ 在 `renderer.py` 新增 class 並註冊；`protocol.py` 的解析規則可能也要跟著改
- [ ] 解碼設定 → `config.json` 的 `decoding`
- [ ] tokenizer → `config.json` 的 `tokenizer.name_or_path`（例如 `OpenFormosa/PangolinTokenizer`）
- [ ] 每題 token 總預算 → `budget.max_total_tokens_per_task`

`BarbetModel` 尚未在真正的 checkpoint 上測試，接上後請先用 `--tasks doc_001` 跑一題確認。

## 已知限制

- 文件題的「找不到」與「矛盾」用關鍵字判斷，題目擴充後要檢查是否有誤判。
- 工具題的成功與否以「最後一次工具呼叫」為準。
- C 組靠工具執行完成的成功會標記為系統能力（`system_assisted`，計算類工具另標 `system_assisted_compute`），不可算成模型原生推理能力。
