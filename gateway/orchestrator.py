import logging
import sys
import os

# Ensure we can import from backend and other directories
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

from .academic_logic.ale_wrapper import AcademicLogicWrapper
from backend.wrappers.kg_wrapper import KGWrapper
from backend.wrappers.rag_wrapper import RAGWrapper
from backend.models.schemas import StructuredQuery, StudentContext, AggregatedResult

logger = logging.getLogger(__name__)

class Orchestrator:
    def __init__(self):
        logger.info("Initializing Orchestrator")
        self._ale = AcademicLogicWrapper()
        self._kg = KGWrapper()
        self._rag = RAGWrapper()

    def _academic_logic_workflow(self, query: StructuredQuery, context: StudentContext) -> AggregatedResult:
        """
        Deterministic workflow for academic rules and math.
        """
        logger.info(f"Executing academic logic workflow for intent: {query.intent}")
        
        result = None
        
        if query.intent == "course_eligibility":
            course_code = query.entities.course_code
            if not course_code:
                return AggregatedResult(
                    original_query=query.intent,
                    engine_pattern="academic_logic",
                    status="error",
                    error_detail="Missing course code for eligibility check"
                )
            
            # Assembler payload: KG fetch + Student Context
            prereqs = self._kg.get_prerequisites(course_code)
            
            payload = {
                "student_completed": [c.course_code for c in context.course_history if c.status == "passed"],
                "student_in_progress": [c.course_code for c in context.course_history if c.status == "in_progress"],
                "target_course": course_code,
                "course_prereqs": prereqs
            }
            result = self._ale.evaluate_eligibility(payload)

        elif query.intent == "graduation_audit":
            payload = {
                "student_context": context.model_dump()
            }
            result = self._ale.evaluate_graduation_audit(payload)

        elif query.intent == "semester_plan":
            # Fetch offerings for the term
            offerings = self._kg.get_courses_offered_in_term("Spring 2026")
            
            payload = {
                "student_context": context.model_dump(),
                "max_credits_allowed": context.max_credit_hours_allowed,
                "available_offerings": offerings
            }
            result = self._ale.generate_semester_plan(payload)

        elif query.intent == "gpa_simulation":
            payload = {
                "current_cgpa": context.cgpa,
                "total_credit_hours_earned": context.total_credit_hours_earned,
                "hypothetical_courses": [
                    {"course_code": c, "credits": 3, "expected_grade_points": 4.0} 
                    for c in query.session_overrides.added_courses
                ]
            }
            result = self._ale.simulate_gpa(payload)

        return AggregatedResult(
            original_query=query.intent,
            engine_pattern="academic_logic",
            kg_result=result,
            student_context=context,
            status="ok"
        )

    def process_query(self, query: StructuredQuery, context: StudentContext) -> AggregatedResult:
        engine_pattern = query.engine_pattern
        
        if engine_pattern == "kg":
            # Placeholder for KG workflow
            return AggregatedResult(status="ok", engine_pattern="kg", original_query="kg query")
        elif engine_pattern == "rag":
            logger.info("Executing RAG workflow")
            rag_output = self._rag.execute(sub_query=query.intent, student_context=context)
            return AggregatedResult(
                original_query=query.intent,
                engine_pattern="rag",
                rag_result=rag_output,
                student_context=context,
                status="ok"
            )
        elif engine_pattern == "academic_logic":
            return self._academic_logic_workflow(query, context)
        
        return AggregatedResult(status="error", error_detail="Invalid engine pattern")
