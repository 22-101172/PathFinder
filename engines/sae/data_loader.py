import pandas as pd
from pathlib import Path

_ROOT = Path(__file__).parent

# Search order: canonical path first, then fall back to variants in root
_CANDIDATE_PATHS = [
    _ROOT / "data" / "students_anonymous.xlsx",
    _ROOT / "data" / "students_anonymous (1).xlsx",
    _ROOT / "students_anonymous.xlsx",
    _ROOT / "students_anonymous (1).xlsx",
]


def _find_data_file() -> Path:
    for p in _CANDIDATE_PATHS:
        if p.exists():
            return p
    searched = "\n  ".join(str(p) for p in _CANDIDATE_PATHS)
    raise FileNotFoundError(
        f"Could not locate the Excel file. Searched:\n  {searched}"
    )


def load_data(path: Path | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Load and clean both sheets from the SAE Excel file.

    Returns
    -------
    students_df : DataFrame
        816 students with complete profiles (183 null-profile rows removed).
    registrations_df : DataFrame
        Full registration/transcript history, linked by ID.
    """
    path = Path(path) if path else _find_data_file()

    students_df = pd.read_excel(path, sheet_name="data")
    registrations_df = pd.read_excel(path, sheet_name="registrations")

    # Drop the trailing empty column openpyxl picks up from the xlsx
    registrations_df = registrations_df.loc[
        :, registrations_df.columns.notna()
    ]
    registrations_df = registrations_df[
        [c for c in registrations_df.columns if not str(c).startswith("Unnamed")]
    ]

    # Remove null-profile rows (students with no Program or Level)
    students_df = (
        students_df
        .dropna(subset=["Program", "Level"])
        .reset_index(drop=True)
    )

    # Keep only registrations for students that survived the filter
    valid_ids = set(students_df["ID"])
    registrations_df = registrations_df[
        registrations_df["ID"].isin(valid_ids)
    ].reset_index(drop=True)

    return students_df, registrations_df


def get_student(
    student_id: str,
    students_df: pd.DataFrame | None = None,
    registrations_df: pd.DataFrame | None = None,
) -> tuple[dict, pd.DataFrame]:
    """
    Return (profile_dict, history_df) for a single student.

    Loads data automatically if DataFrames are not supplied.
    """
    if students_df is None or registrations_df is None:
        students_df, registrations_df = load_data()

    mask = students_df["ID"] == student_id
    if not mask.any():
        raise KeyError(f"Student {student_id!r} not found (or was filtered as null-profile).")

    profile = students_df[mask].iloc[0].to_dict()
    history = registrations_df[registrations_df["ID"] == student_id].copy()
    return profile, history
