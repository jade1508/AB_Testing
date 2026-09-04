"""
pipeline.py — Automated Config-Driven A/B Testing Engine
Executes via GitHub Actions (Schedule / Repository Dispatch / Manual).
Reads pending experiments from Registry, computes statistical metrics & SRM,
and pushes standardized JSON payloads to Make.com Webhook.
"""

import hashlib
import os
import sys
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import requests
from scipy import stats
from statsmodels.stats.proportion import proportions_ztest


# ------------------------------------------------------------------------------
# 1. HELPER FUNCTIONS FOR STATISTICAL ENGINE & AUDIT TRAIL
# ------------------------------------------------------------------------------
def compute_checksum(df: pd.DataFrame) -> str:
    """Calculates SHA256 checksum of raw dataset for audit trail."""
    return hashlib.sha256(pd.util.hash_pandas_object(df).values).hexdigest()[:12]


def run_ab_analysis(config: dict) -> dict:
    """
    Executes statistical evaluation for a single experiment configuration.
    Supports both 'binary' (Conversion/Retention) and 'continuous' (Revenue/AOV) metrics.
    """
    exp_id = config["experiment_id"]
    source = config["dataset_source"]
    group_col = config["group_column"]
    metric_col = config["metric_column"]
    metric_type = config.get("metric_type", "binary").lower()
    expected_split = [
        float(x) for x in str(config.get("expected_split", "0.5/0.5")).split("/")
    ]

    # Load dataset (Local path or Remote URL)
    if not os.path.exists(source) and not source.startswith(("http://", "https://")):
        raise FileNotFoundError(f"Dataset source not found: {source}")

    df = pd.read_csv(source)
    checksum = compute_checksum(df)

    # Clean & split groups
    groups = df[group_col].dropna().unique()
    if len(groups) != 2:
        raise ValueError(
            f"Experiment {exp_id} expects exactly 2 groups in '{group_col}', found: {groups}"
        )

    # Sort groups to maintain consistency (Control vs Treatment)
    control_label, treatment_label = sorted(groups)
    control_data = df[df[group_col] == control_label][metric_col].dropna()
    treatment_data = df[df[group_col] == treatment_label][metric_col].dropna()

    n_control = len(control_data)
    n_treatment = len(treatment_data)
    n_total = n_control + n_treatment

    # --- A. Guardrail Check: Sample Ratio Mismatch (SRM) ---
    observed = [n_control, n_treatment]
    expected = [n_total * expected_split[0], n_total * expected_split[1]]
    _, p_srm = stats.chisquare(f_obs=observed, f_exp=expected)
    srm_passed = bool(p_srm >= float(config.get("srm_threshold", 0.001)))

    # --- B. Primary Metric Evaluation ---
    if metric_type == "binary":
        succ_control = int(control_data.sum())
        succ_treatment = int(treatment_data.sum())

        val_control = float(control_data.mean())
        val_treatment = float(treatment_data.mean())

        # Two-Sample Z-Test
        _, p_value = proportions_ztest(
            [succ_treatment, succ_control], [n_treatment, n_control]
        )

    elif metric_type == "continuous":
        val_control = float(control_data.mean())
        val_treatment = float(treatment_data.mean())

        # Welch's T-Test (Equal variance not assumed)
        _, p_value = stats.ttest_ind(
            treatment_data, control_data, equal_var=False
        )
    else:
        raise ValueError(f"Unsupported metric_type: {metric_type}")

    absolute_lift = val_treatment - val_control
    relative_lift = (
        (absolute_lift / val_control) * 100 if val_control != 0 else 0.0
    )
    alpha = float(config.get("alpha", 0.05))
    is_stat_sig = bool(p_value < alpha)

    # --- C. Decision Rule Engine ---
    if not srm_passed:
        decision = "INVALID_TEST_SRM"
    elif is_stat_sig and absolute_lift > 0:
        decision = "SHIP"
    else:
        decision = "DO_NOT_SHIP"

    return {
        "run_status": "success",
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "dataset_checksum": checksum,
        "experiment_id": exp_id,
        "experiment_name": config.get("experiment_name", exp_id),
        "business_context": config.get("business_context", "General A/B Test"),
        "owner_email": config.get("owner_email", ""),
        "metric_name": metric_col,
        "metric_type": metric_type,
        "sample_size_total": n_total,
        "sample_size_control": n_control,
        "sample_size_treatment": n_treatment,
        "val_control": round(val_control, 4),
        "val_treatment": round(val_treatment, 4),
        "absolute_lift": round(absolute_lift, 4),
        "relative_lift_pct": round(relative_lift, 2),
        "p_value": round(float(p_value), 4),
        "p_srm": round(float(p_srm), 4),
        "srm_passed": srm_passed,
        "is_stat_sig": is_stat_sig,
        "decision": decision,
    }


# ------------------------------------------------------------------------------
# 2. MAIN ORCHESTRATOR
# ------------------------------------------------------------------------------
def main():
    webhook_url = os.getenv("MAKE_WEBHOOK_URL")
    if not webhook_url:
        print("❌ Error: MAKE_WEBHOOK_URL secret is not set in GitHub Secrets.")
        sys.exit(1)

    registry_path = os.getenv("REGISTRY_PATH", "registry.csv")
    if not os.path.exists(registry_path):
        print(f"❌ Error: Registry file '{registry_path}' not found.")
        sys.exit(1)

    print(f"📋 Reading Experiment Registry from: {registry_path}")
    registry_df = pd.read_csv(registry_path)

    # Filter active/pending experiments
    pending_exps = registry_df[
        registry_df["status"].str.lower() == "pending"
    ].to_dict(orient="records")

    if not pending_exps:
        print("ℹ️ No pending experiments found. Pipeline complete.")
        return

    print(f"🚀 Found {len(pending_exps)} pending experiment(s) to process...\n")

    had_failure = False
    for config in pending_exps:
        exp_id = config.get("experiment_id", "UNKNOWN")
        print(f"🔄 Executing Analysis for: [{exp_id}]...")

        try:
            result = run_ab_analysis(config)

            # Send JSON Payload to Make.com
            response = requests.post(webhook_url, json=result, timeout=15)

            if response.status_code in [200, 201, 204]:
                print(
                    f"✅ Successfully processed & pushed [{exp_id}] to Make.com Webhook."
                )
            else:
                print(
                    f"❌ Webhook failed for [{exp_id}] with status code: {response.status_code}"
                )
                had_failure = True

        except Exception as e:
            print(f"❌ Error executing experiment [{exp_id}]: {str(e)}")
            had_failure = True

    if had_failure:
        print("\n⚠️ Pipeline completed with one or more errors.")
        sys.exit(1)
    else:
        print("\n🎉 All experiments executed and delivered successfully!")


if __name__ == "__main__":
    main()