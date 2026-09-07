# A/B Testing Pipeline - A Config-Driven, Scalable Experimentation Engine

A fully automated A/B test analysis pipeline that scales to *any* number of
experiments by adding a row to a spreadsheet - no new code required per test.
Inspired by *Trustworthy Online Controlled Experiments* (Kohavi, Tang, Xu).

The core idea: **AI writes the report. Code - not AI - makes the call.**
Every ship / no-ship decision is computed deterministically in Python before
the payload ever reaches the AI step; the AI's only job is to turn verified
numbers into a clear, executive-ready narrative.

---

## 1. What changed from the single-experiment prototype

The original version of this project hardcoded one dataset (Cookie Cats) into
one script. This version separates **what to test** (declared in a registry)
from **how to test it** (one reusable engine) - the same pattern used by
real experimentation platforms (Optimizely, GrowthBook).

Adding a new A/B test to the pipeline now means adding one row to
`registry.csv` - not writing new code.

## 2. Experiment Registry (`registry.csv`)

| Column | Purpose |
|---|---|
| `experiment_id` | Unique key |
| `experiment_name` | Human-readable label |
| `business_context` | Free-text tag (e.g. "Mobile Gaming Retention") |
| `dataset_source` | Path/URL to the dataset |
| `group_column` | Column holding the Control/Treatment label |
| `control_label` | Explicit value marking the Control group (falls back to alphabetical sort if omitted - logged as a warning when that happens) |
| `metric_column` | Column holding the outcome to compare |
| `metric_type` | `binary` (proportion z-test) or `continuous` (Welch's t-test) |
| `expected_split` | Expected traffic ratio, e.g. `0.5/0.5` |
| `alpha` | Significance threshold (default 0.05) |
| `srm_threshold` | SRM alert threshold (default 0.001) |
| `owner_email` | Who owns this experiment |
| `status` | `pending` / `skipped` / `done` - pipeline only runs `pending` rows |

Current registry:
```
cookie_cats_01,Cookie Cats Gate 30 vs 40,Mobile Gaming Retention,cookie_cats.csv,version,gate_30,retention_7,binary,0.5/0.5,0.05,0.001,cpo@company.com,pending
pricing_test_02,Freemium Trial Checkout,E-commerce Pricing,pricing_data.csv,group,baseline,converted,binary,0.5/0.5,0.05,0.001,growth@company.com,skipped
```
`pricing_test_02` is configured but marked `skipped` - included to show the
registry supports multiple experiments side by side, not as a second
verified result. Only `cookie_cats_01` has been run end-to-end.

## 3. Architecture

```
registry.csv (Experiment Registry)
   │  filtered to status == "pending"
   ▼
pipeline_expansion.py
   │  for each pending row: run_ab_analysis(config)
   │  ├─ resolve control/treatment via explicit control_label
   │  ├─ SRM check (chi-square)
   │  ├─ significance test (proportion z-test or Welch's t-test)
   │  ├─ decision rule (computed in Python, not AI)
   │  └─ SHA-256 dataset checksum (audit trail)
   ▼
GitHub Actions (schedule: daily 08:00 UTC | workflow_dispatch | repository_dispatch)
   ▼
Webhook → Make.com AI Toolkit (writes the narrative brief, does not decide)
   ▼
Router
   ├──► Google Sheets - one row per run, filterable by Experiment ID
   └──► Gmail - automated executive email
```

## 4. Methodology

### 4.1 Sample Ratio Mismatch (SRM) Check
Chi-square goodness-of-fit test, alert threshold **p < 0.001** (configurable
per experiment via `srm_threshold`) - stricter than the conventional 0.05,
because SRM signals a broken randomization process, not a business effect.
If SRM fails, the decision is `INVALID_TEST_SRM` and no other result is
trusted.

### 4.2 Significance Test
- `binary` metrics (retention, conversion) → two-proportion Z-test
- `continuous` metrics (revenue, AOV) → Welch's t-test

### 4.3 Control/Treatment Resolution
The engine uses the `control_label` declared in the registry, not
alphabetical order. If `control_label` is missing or doesn't match either
group value in the dataset, it falls back to alphabetical sorting and logs
a warning - so a misconfigured registry row is visible in the run logs
rather than silently mislabeling groups.

### 4.4 Decision Logic - computed in Python, not by the AI
```python
if not srm_passed:
    decision = "INVALID_TEST_SRM"
elif is_stat_sig and absolute_lift > 0:
    decision = "SHIP"
else:
    decision = "DO_NOT_SHIP"
```
This runs before the result is sent to Make.com. The AI Toolkit step only
formats this pre-computed decision into an executive narrative.

### 4.5 Audit Trail
Each run stores a 12-character SHA-256 checksum of the input dataset
(`dataset_checksum`), so any run can be traced back to the exact data
snapshot it was computed from.

## 5. Results (verified run - `cookie_cats_01`)

| Metric | Control (gate_30) | Treatment (gate_40) |
|---|---|---|
| 7-Day Retention | 19.02% | 18.20% |
| Sample size | 29,829 | 30,270 |

- **SRM check:** p = 0.0086 → ✅ Passed
- **Significance test:** p = 0.0016 → statistically significant
- **Decision:** 🟡 **DO NOT SHIP** - statistically real difference, wrong
  direction (retention dropped)

> Note: sample size in this run (60,099 total) is smaller than the full
> public Cookie Cats dataset (~90,189 users). This reflects the current
> `cookie_cats.csv` snapshot in this repo - re-verify against the latest
> pipeline run before quoting these numbers elsewhere.

## 6. What this project is - and isn't

- ✅ Config-driven: new experiments are added via `registry.csv`, not new code
- ✅ Control/Treatment resolved via explicit config, with a logged fallback
- ✅ Decision logic is deterministic and computed before the AI step
- ✅ Audit trail via dataset checksum
- ✅ Verified end-to-end on one real dataset (Cookie Cats)
- ⚠️ A second experiment (`pricing_test_02`) is configured but intentionally
  not yet run (`status: skipped`)
- ⚠️ Single-look statistical test - no sequential/peeking-safe monitoring yet
- ⚠️ Status transitions (`pending` → `done`) are currently manual

## 7. Tech Stack

- **Analysis:** Python (pandas, scipy, statsmodels, hashlib)
- **Automation:** GitHub Actions (`schedule`, `workflow_dispatch`,
  `repository_dispatch` triggers), Make.com (webhook, AI Toolkit, Router,
  Google Sheets, Gmail)
- **Config:** `registry.csv` (spreadsheet-based experiment registry)

## 8. Credits

Inspired by *Trustworthy Online Controlled Experiments: A Practical Guide
to A/B Testing* by Ron Kohavi, Diane Tang, and Ya Xu.
