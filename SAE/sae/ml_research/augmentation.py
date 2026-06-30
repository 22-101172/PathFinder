"""
Data augmentation pipeline for SAE graduation prediction.

Label definition
----------------
label = 1  student graduated AND semesters_count <= ON_TIME_THRESHOLD (10)
label = 0  active/studying students, OR graduates who took > 10 semesters

This overrides the raw ``is_graduated`` field from feature_engineering, which
only captures whether a student graduated at all.  The redefined label targets
the harder, more actionable question: "will this student graduate on time?"

Feature set
-----------
credit_pace_ratio is intentionally excluded: it has near-zero variance among
graduated students (all 36 show exactly 1.1083) so it carries no signal and
would mask real predictors.

SMOTE is applied to the TRAINING SPLIT ONLY.  The test set and CV test folds
always contain real data so reported metrics are not inflated.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from imblearn.over_sampling import SMOTE
from sklearn.model_selection import train_test_split

from sae.data_loader import load_data
from sae.feature_engineering import build_features

# ── Constants ─────────────────────────────────────────────────────────────────
FEATURE_COLS: list[str] = [
    "gpa_slope",
    "failed_core_rate",
    "repeat_course_rate",
    "warning_risk",
    "pass_rate",
]

LABEL_COL = "label"
ON_TIME_THRESHOLD = 10       # semesters; <=10 → graduated on time
TEST_SIZE = 0.20
RANDOM_STATE = 42

_ACTIVE_LEVELS = {"Freshman", "Sophomore", "Junior", "Senior"}


# ── Return type ───────────────────────────────────────────────────────────────
@dataclass
class DataBundle:
    """Holds the fully prepared train/test split for the trainer."""
    X_train: pd.DataFrame   # SMOTE-expanded training features
    X_test: pd.DataFrame    # real-data test features (never touched by SMOTE)
    y_train: pd.Series      # SMOTE-expanded training labels
    y_test: pd.Series       # real-data test labels
    X_real: pd.DataFrame    # full pre-SMOTE feature set (used for honest CV)
    y_real: pd.Series       # full pre-SMOTE labels      (used for honest CV)
    feature_cols: list[str]


# ── Helpers ───────────────────────────────────────────────────────────────────
def _semester_counts(registrations_df: pd.DataFrame) -> pd.Series:
    """Return ID -> number of distinct semesters in transcript."""
    return (
        registrations_df
        .groupby("ID")["Semester"]
        .nunique()
        .rename("semesters_count")
    )


def _print_dist(title: str, y: pd.Series) -> None:
    counts = y.value_counts().sort_index()
    print(f"\n  {title}")
    print(f"    label=0 : {counts.get(0, 0):>4d}")
    print(f"    label=1 : {counts.get(1, 0):>4d}")
    print(f"    total   : {len(y):>4d}")


# ── Main entry point ──────────────────────────────────────────────────────────
def prepare_dataset(
    students_df: pd.DataFrame | None = None,
    registrations_df: pd.DataFrame | None = None,
    features_df: pd.DataFrame | None = None,
    random_state: int = RANDOM_STATE,
    verbose: bool = True,
) -> DataBundle:
    """
    Build the augmented dataset ready for model training.

    Parameters
    ----------
    students_df, registrations_df, features_df
        Supply pre-loaded DataFrames to avoid re-reading the file.
        All three are loaded automatically if omitted.
    random_state
        Seed for both the train/test split and SMOTE.
    verbose
        Print cohort sizes and class distribution if True.

    Returns
    -------
    DataBundle
    """
    if students_df is None or registrations_df is None:
        students_df, registrations_df = load_data()
    if features_df is None:
        features_df = build_features(students_df, registrations_df)

    # ── 1. Base cohort ─────────────────────────────────────────────────────
    profile = students_df[["ID", "Study Status", "Level"]].set_index("ID")
    cohort = features_df.join(profile, how="left")

    # ── 2. Compute semesters_count; assign label ───────────────────────────
    sem_counts = _semester_counts(registrations_df)
    cohort = cohort.join(sem_counts, how="left")
    cohort["semesters_count"] = cohort["semesters_count"].fillna(0).astype(int)

    # Filter to known outcomes only:
    # 1. Graduated
    # 2. Active but already exceeded the on-time threshold
    graduated_mask = cohort["Study Status"] == "Graduated"
    active_over_time = (
        (cohort["Study Status"] == "Studying") 
        & (cohort["semesters_count"] > ON_TIME_THRESHOLD)
    )
    cohort = cohort[graduated_mask | active_over_time].copy()

    cohort[LABEL_COL] = (
        (cohort["Study Status"] == "Graduated")
        & (cohort["semesters_count"] <= ON_TIME_THRESHOLD)
    ).astype(int)

    # ── 3. Drop null-pass_rate rows ────────────────────────────────────────
    null_mask = cohort["pass_rate"].isna()
    n_dropped = int(null_mask.sum())
    cohort = cohort[~null_mask]

    # ── 4. Class distribution before SMOTE ────────────────────────────────
    if verbose:
        print("=" * 55)
        print("COHORT SUMMARY")
        print("=" * 55)
        grad = cohort[cohort["Study Status"] == "Graduated"]
        active = cohort[cohort["Study Status"] == "Studying"]
        print(f"  Graduated (all)           : {len(grad)}")
        print(f"    label=1 (<=10 semesters): {(grad[LABEL_COL] == 1).sum()}")
        print(f"    label=0 (>10  semesters): {(grad[LABEL_COL] == 0).sum()}")
        print(f"  Active (>10 semesters)    : {len(active)}")
        print(f"  Dropped (null pass_rate)  : {n_dropped}")
        print(f"  Training pool total       : {len(cohort)}")

        _print_dist("Class distribution BEFORE SMOTE (full dataset)", cohort[LABEL_COL])

    # ── 5. Train / test split (stratified) ────────────────────────────────
    X = cohort[FEATURE_COLS].copy()
    y = cohort[LABEL_COL].copy()

    X_real = X.copy()
    y_real = y.copy()

    X_tr_raw, X_test, y_tr_raw, y_test = train_test_split(
        X, y,
        test_size=TEST_SIZE,
        stratify=y,
        random_state=random_state,
    )

    # ── 6. SMOTE on training split only ───────────────────────────────────
    n_train_minority = int((y_tr_raw == 1).sum())

    if n_train_minority < 2:
        raise ValueError(
            f"Only {n_train_minority} minority sample(s) in training split — "
            "cannot apply SMOTE.  Reduce TEST_SIZE or add more positive examples."
        )

    # k_neighbors must be strictly less than minority count in training
    k_neighbors = min(5, n_train_minority - 1)

    smote = SMOTE(
        sampling_strategy="auto",
        k_neighbors=k_neighbors,
        random_state=random_state,
    )
    X_tr_arr, y_tr_arr = smote.fit_resample(X_tr_raw, y_tr_raw)
    X_train = pd.DataFrame(X_tr_arr, columns=FEATURE_COLS)
    y_train = pd.Series(y_tr_arr, name=LABEL_COL)

    if verbose:
        print()
        print("=" * 55)
        print("TRAIN / TEST SPLIT + SMOTE")
        print("=" * 55)
        print(f"  Split: {int((1-TEST_SIZE)*100)}/{int(TEST_SIZE*100)} "
              f"stratified (seed={random_state})")
        _print_dist("Training set BEFORE SMOTE", y_tr_raw)
        _print_dist(f"Training set AFTER  SMOTE (target='auto')", y_train)
        _print_dist("Test set (real data, untouched)", y_test)
        print(f"\n  SMOTE k_neighbors used : {k_neighbors}")

    return DataBundle(
        X_train=X_train,
        X_test=X_test,
        y_train=y_train,
        y_test=y_test,
        X_real=X_real,
        y_real=y_real,
        feature_cols=FEATURE_COLS,
    )
