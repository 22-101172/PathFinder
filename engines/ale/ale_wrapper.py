import logging
from typing import Dict, Any

from engines.ale.a3_eligibility    import EligibilityChecker
from engines.ale.a4_graduation_audit import GraduationAudit
from engines.ale.a5_semester_plan  import SemesterPlanner
from engines.ale.c2_gpa_simulation import GPASimulator

logger = logging.getLogger(__name__)


class AcademicLogicWrapper:
    """
    Public API for the Academic Logic Engine.

    This is the ONLY entry point the Orchestrator should call.
    Each method accepts a structured Dict payload and returns a
    structured Dict result conforming to the ALE standard output schema.
    """

    def __init__(self):
        logger.info("AcademicLogicWrapper initialized")

    def check_eligibility(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """A3 — Course Eligibility Check."""
        logger.info("ALE: check_eligibility → A3")
        return EligibilityChecker.process(payload)

    def run_graduation_audit(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """A4 — Graduation Audit."""
        logger.info("ALE: run_graduation_audit → A4")
        return GraduationAudit.process(payload)

    def generate_semester_plan(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """A5 — Semester Course Plan Generation."""
        logger.info("ALE: generate_semester_plan → A5")
        return SemesterPlanner.process(payload)

    def simulate_gpa(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """C2 — GPA Simulation (What-if Calculation)."""
        logger.info("ALE: simulate_gpa → C2")
        return GPASimulator.process(payload)
