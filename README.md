# Barbet Harness：Skill、記憶檢索、工具與有限次修正

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/cathy910207-hub/barbet-harness/blob/main/run.ipynb)

驗證「模型權重不夠強時，能否靠外部記憶、skill、工具結果與驗證回饋，更可靠地完成任務」。

> ⚠️ 目前的模型是 **DUMMY 假模型**，只用來確認流程正確。所有數字都不是 Barbet 的結果。
> 取得主線的 checkpoint、renderer、解碼設定之前，不要用其他模型代替並稱為 Barbet 結果。

## 快速開始

```bash
python tests/test_pipeline.py                                  # 檢查流程（應顯示「全部檢查通過」）
python run_experiment.py --dummy-script tests/dummy_script.json # 用假模型劇本跑 A/B/C
```

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
  grader.py            評分（唯一讀標準答案的地方，結果不回饋給模型）
  metrics.py           統計與報告
data/
  tasks.jsonl          題庫
  docs/  skills/  tools/
tests/
  test_pipeline.py     流程檢查
  dummy_script.json    假模型劇本
```

## 防止標準答案洩漏的設計

1. `runner.public_view()` 會把題目的 `gold` 移除，renderer 只拿得到移除後的版本。
2. `verifier.py` 只用 schema、工具結果、文件庫段落清單、skill 輸出格式產生回饋，不 import 評分器。
3. `grader.py` 的評分結果只寫進 trace（欄位 `attempt_grades_for_analysis_only`），從不放回 prompt。
4. `tests/test_pipeline.py` 會檢查每一個 prompt 都不含標準答案。

## 題目格式（tasks.jsonl，一行一題）

```json
{"id": "tool_001", "category": "tool_selection",
 "input": {"query": "幫我把 100 美元換成台幣。", "tools": ["convert_currency", "get_order_status"]},
 "gold": {"expect": "tool", "tool": "convert_currency",
          "args": {"amount": 100, "from_currency": "USD", "to_currency": "TWD"},
          "result": {"converted_amount": 3200.0}},
 "tags": ["normal"]}
```

`category`：`doc_retrieval`／`rule_following`／`tool_selection`／`interpret_fix`

`gold.expect` 可用的值：

| expect | 用途 | 需要的欄位 |
|---|---|---|
| `answer` | 一般回答（文件題、規則題） | 文件題：`answer_contains`、`sources`；規則題：`answer_exact` |
| `not_found` | 文件中找不到，不可編造 | — |
| `contradiction` | 文件矛盾，需指出 | `sources`（所有矛盾來源） |
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
| skill 替換 | 同一個 `query`、不同 `skill_id` 與不同 `gold`（如 rule_001／rule_002），tag 標 `skill_swap_pair` |
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
