"""
Model trainer for SAE graduation prediction.

Evaluation strategy
-------------------
RepeatedStratifiedKFold (n_splits=5, n_repeats=10) -> 50 evaluation folds.
SMOTE is inside an imblearn Pipeline so the SMOTE fit sees only the training
fold; the test fold is always untouched real data.

Final model
-----------
Trained on ALL 718 real students after SMOTE is applied to the full set.
The CV metrics are the honest estimate of generalisation; the final model
maximises data used for fitting.

Saved artefact
--------------
sae/saved_models/graduation_model.pkl contains a dict:
    {"model": RandomForestClassifier, "feature_cols": list, "importances": dict}

Run directly:  python -m sae.trainer
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from imblearn.over_sampling import SMOTE
from imblearn.pipeline import Pipeline as ImbPipeline
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import make_scorer, precision_score, recall_score, f1_score
from sklearn.model_selection import RepeatedStratifiedKFold, cross_validate

from engines.sae.augmentation import (
    DataBundle,
    FEATURE_COLS,
    prepare_dataset,
)
from engines.sae.data_loader import load_data
from engines.sae.feature_engineering import build_features, compute_student_features
from engines.sae.rule_engine import DEFAULT_RULES, f3_at_risk_score

MODEL_PATH = Path(__file__).parent / "saved_models" / "graduation_model.pkl"

# Per-feature: direction where the value becomes a RISK (used by predict_graduation)
_RISK_DIRECTION = {
    "gpa_slope":           "lower",   # more negative = more risk
    "failed_core_rate":    "higher",
    "repeat_course_rate":  "higher",
    "warning_risk":        "higher",
    "pass_rate":           "lower",
}

_FEATURE_LABELS = {
    "gpa_slope":           "declining GPA trend",
    "failed_core_rate":    "high core failure rate",
    "repeat_course_rate":  "high course repetition rate",
    "warning_risk":        "academic warning on record",
    "pass_rate":           "low pass rate",
}

# Normalisation denominators derived from the real data distribution
_RISK_NORM = {
    "gpa_slope":           0.50,   # treat slope <= -0.5 as maximum risk
    "failed_core_rate":    0.30,   # treat >= 30% failure rate as max risk
    "repeat_course_rate":  0.40,   # treat >= 40% repeat rate as max risk
    "warning_risk":        1.0,
    "pass_rate":           1.0,    # risk = 1 - pass_rate
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_rf() -> RandomForestClassifier:
    return RandomForestClassifier(
        n_estimators=200,
        max_depth=None,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,
    )


def _safe_k(n_minority: int, n_splits: int = 5) -> int:
    """Largest k_neighbors that is safe for the smallest CV training fold."""
    min_train = n_minority * (n_splits - 1) // n_splits
    return max(1, min(5, min_train - 1))


def _build_cv_pipeline(k_neighbors: int) -> ImbPipeline:
    """Pipeline that applies SMOTE only inside each training fold."""
    return ImbPipeline([
        ("smote", SMOTE(
            sampling_strategy="auto",
            k_neighbors=k_neighbors,
            random_state=42,
        )),
        ("clf", _build_rf()),
    ])


def _risk_contributions(
    raw_feats: dict,
    importances: pd.Series,
) -> list[dict[str, Any]]:
    """
    Compute a per-feature risk contribution score for one student.

    Score = feature_importance * normalised_risk_value.
    Returned list is sorted descending by score.
    """
    results = []
    for feat in FEATURE_COLS:
        val = float(raw_feats.get(feat) or 0.0)
        imp = float(importances.get(feat, 0.0))
        denom = _RISK_NORM[feat]

        if _RISK_DIRECTION[feat] == "lower":
            # risk increases as value drops (pass_rate) or goes negative (gpa_slope)
            raw_risk = max(0.0, -val if feat == "gpa_slope" else 1.0 - val)
        else:
            raw_risk = max(0.0, val)

        norm_risk = min(raw_risk / denom, 1.0)
        score = imp * norm_risk
        results.append({
            "feature":       feat,
            "description":   _FEATURE_LABELS[feat],
            "risk_score":    round(score, 4),
            "student_value": raw_feats.get(feat),
        })

    results.sort(key=lambda x: x["risk_score"], reverse=True)
    return results


# ── Main training / evaluation ────────────────────────────────────────────────

def train_and_evaluate(
    bundle: DataBundle | None = None,
    verbose: bool = True,
) -> tuple[RandomForestClassifier, dict]:
    """
    Evaluate with RepeatedStratifiedKFold, then train final model on all data.

    Parameters
    ----------
    bundle
        Pre-built DataBundle from augmentation.prepare_dataset().
        Built automatically (from the Excel file) if omitted.
    verbose
        Print all evaluation tables if True.

    Returns
    -------
    (final_fitted_model, cv_results_dict)
    """
    if bundle is None:
        students_df, registrations_df = load_data()
        features_df = build_features(students_df, registrations_df)
        bundle = prepare_dataset(
            students_df, registrations_df, features_df, verbose=verbose
        )

    X_real = bundle.X_real   # 718 real students, no augmentation
    y_real = bundle.y_real

    n_minority = int((y_real == 1).sum())
    k = _safe_k(n_minority, n_splits=5)

    # ── 1. RepeatedStratifiedKFold CV (50 folds) ──────────────────────────
    # Cross-validation is done on real data only.
    # SMOTE is applied only to the training fold inside each CV split via the
    # imblearn Pipeline — test folds are NEVER resampled.
    cv_pipeline = _build_cv_pipeline(k_neighbors=k)
    rskf = RepeatedStratifiedKFold(n_splits=5, n_repeats=10, random_state=42)

    scoring = {
        "accuracy":  "accuracy",
        "precision": make_scorer(precision_score, zero_division=0),
        "recall":    make_scorer(recall_score,    zero_division=0),
        "f1":        make_scorer(f1_score,        zero_division=0),
        "roc_auc":   "roc_auc",
    }

    if verbose:
        print("\n" + "=" * 65)
        print("REPEATED STRATIFIED K-FOLD CROSS-VALIDATION")
        print("n_splits=5  n_repeats=10  =>  50 evaluation folds total")
        print("SMOTE applied ONLY inside training folds (imblearn Pipeline)")
        print("=" * 65)
        print(f"  Real dataset  : {len(X_real)} students")
        print(f"  Minority      : {n_minority}   Majority: {int((y_real==0).sum())}")
        print(f"  SMOTE target  : 'auto' (balance classes)")
        print(f"  k_neighbors   : {k}  (safe for ~{n_minority*(5-1)//5} train-fold minority)")
        print()

    cv_results = cross_validate(
        cv_pipeline,
        X_real,
        y_real,
        cv=rskf,
        scoring=scoring,
        return_train_score=False,
        n_jobs=-1,
    )

    if verbose:
        print(f"  {'Metric':<12} {'Mean':>8} {'Std':>8} {'Min':>8} {'Max':>8}")
        print(f"  {'-'*12} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")
        for m in ["accuracy", "precision", "recall", "f1", "roc_auc"]:
            s = cv_results[f"test_{m}"]
            print(f"  {m:<12} {s.mean():>8.4f} {s.std():>8.4f}"
                  f" {s.min():>8.4f} {s.max():>8.4f}")

    # ── 2. Final model on ALL real data + SMOTE ───────────────────────────
    # After honest CV, we train on the full dataset to maximise sample coverage.
    # This model is what gets saved and used for live predictions.
    smote_full = SMOTE(
        sampling_strategy="auto",
        k_neighbors=k,
        random_state=42,
    )
    X_aug, y_aug = smote_full.fit_resample(X_real, y_real)
    X_aug = pd.DataFrame(X_aug, columns=FEATURE_COLS)
    y_aug = pd.Series(y_aug, name="label")

    final_clf = _build_rf()
    final_clf.fit(X_aug, y_aug)

    importances = pd.Series(
        final_clf.feature_importances_, index=FEATURE_COLS
    ).sort_values(ascending=False)

    if verbose:
        aug_dist = y_aug.value_counts().sort_index()
        print()
        print("=" * 65)
        print(f"FINAL MODEL  (all {len(X_real)} real students + SMOTE)")
        print("=" * 65)
        print(f"  Training set after SMOTE:  "
              f"label=0: {aug_dist.get(0,0)}   label=1: {aug_dist.get(1,0)}")
        print()
        print("  Feature importance:")
        max_imp = importances.max()
        for feat, imp in importances.items():
            bar = "#" * int(imp / max_imp * 35)
            print(f"    {feat:<22}: {imp:.4f}  {bar}")

    # ── 3. Save model artefact ────────────────────────────────────────────
    cv_summary = {
        metric: {
            "mean": float(cv_results[f"test_{metric}"].mean()),
            "std":  float(cv_results[f"test_{metric}"].std()),
        }
        for metric in ["accuracy", "precision", "recall", "f1", "roc_auc"]
    }

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model":        final_clf,
            "feature_cols": FEATURE_COLS,
            "importances":  importances.to_dict(),
            "cv_summary":   cv_summary,
        },
        MODEL_PATH,
    )

    if verbose:
        print(f"\n  Saved -> {MODEL_PATH}")

    return final_clf, cv_results


# ── Prediction API ────────────────────────────────────────────────────────────

def predict_graduation(
    student_id: str,
    rules: dict = DEFAULT_RULES,
    students_df: pd.DataFrame | None = None,
    registrations_df: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """
    Predict on-time graduation probability for a single student.

    Parameters
    ----------
    student_id
        Student ID string, e.g. 'STU000026'.
    rules
        Rule engine threshold dict. Pass a RAG-retrieved dict to override
        thresholds at runtime without retraining the model.
    students_df, registrations_df
        Pre-loaded DataFrames to avoid re-reading the Excel file on each call.
        Loaded automatically if omitted.

    Returns
    -------
    dict
        student_id       : str
        probability      : float  – 0-1 probability of on-time graduation
        prediction       : int    – 1 = predicted on-time, 0 = not
        top_risk_factors : list[dict]  – top 2 contributors with risk_score
        explanation      : str    – plain English summary
    """
    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f"No saved model at {MODEL_PATH}. Run train_and_evaluate() first."
        )

    saved = joblib.load(MODEL_PATH)
    clf: RandomForestClassifier = saved["model"]
    importances = pd.Series(saved["importances"])

    if students_df is None or registrations_df is None:
        students_df, registrations_df = load_data()

    if not (students_df["ID"] == student_id).any():
        raise KeyError(
            f"Student {student_id!r} not found or was filtered as null-profile."
        )

    student_row = students_df[students_df["ID"] == student_id].iloc[0]
    regs = registrations_df[registrations_df["ID"] == student_id]
    raw_feats = compute_student_features(student_row, regs)

    # Build the feature vector in the exact column order the model was trained on
    x = pd.DataFrame(
        [[float(raw_feats.get(f) or 0.0) for f in FEATURE_COLS]],
        columns=FEATURE_COLS,
    )

    prob = float(clf.predict_proba(x)[0, 1])
    prediction = int(clf.predict(x)[0])

    # Per-student risk contributions (importance × normalised risk value)
    contributions = _risk_contributions(raw_feats, importances)
    top2 = [c for c in contributions[:2] if c["risk_score"] > 0]

    # Rule engine context (uses merged profile + features dict)
    merged = {**student_row.to_dict(), **raw_feats}
    re_result = f3_at_risk_score(merged, rules)

    # Plain English explanation
    level  = student_row.get("Level",        "student")
    status = student_row.get("Study Status", "unknown status")

    if prediction == 1:
        verdict = (
            f"Student {student_id} ({level}, {status}) is predicted to graduate "
            f"on time with {prob*100:.1f}% model confidence."
        )
    else:
        verdict = (
            f"Student {student_id} ({level}, {status}) is predicted to NOT graduate "
            f"on time (model confidence for on-time: {prob*100:.1f}%)."
        )

    if top2:
        factors = " and ".join(f["description"] for f in top2)
        risk_note = (
            f" Top contributing risk factors: {factors}."
            if prediction == 0 or any(f["risk_score"] > 0.05 for f in top2)
            else ""
        )
    else:
        risk_note = " No notable risk factors detected."

    re_note = (
        f" Rule engine: {re_result['risk_level']} risk "
        f"(score {re_result['score']}/100"
        + (f", triggered: {list(re_result['breakdown'])}" if re_result["breakdown"] else "")
        + ")."
    )

    return {
        "student_id":       student_id,
        "probability":      round(prob, 4),
        "prediction":       prediction,
        "top_risk_factors": top2,
        "explanation":      verdict + risk_note + re_note,
    }


if __name__ == "__main__":
    train_and_evaluate()
