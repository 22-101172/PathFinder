"""
SAEAdapter — connects PathFinder's FastAPI backend to the
standalone Student Analysis Engine (SAE) service over HTTP.

SAE owns its own student data, course catalogue, and analytics.
This adapter only needs: a student/advisor ID, and (optionally)
a rules bundle fetched from RAG.

Run SAE separately:
    cd SAE/
    uvicorn sae.api:app --port 8502

Configure in PathFinder's .env:
    SAE_BASE_URL=http://localhost:8502
"""
from __future__ import annotations
import json
import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)

SAE_BASE_URL = os.getenv("SAE_BASE_URL", "http://localhost:8502")
SAE_TIMEOUT = int(os.getenv("SAE_TIMEOUT_SECONDS", "30"))


class SAEAdapter:
    def __init__(self, base_url: str = SAE_BASE_URL, timeout: int = SAE_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        try:
            r = requests.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.ConnectionError:
            logger.warning("SAE unreachable at %s%s", self.base_url, path)
            return {"error": "SAE service unavailable", "status": "connection_error"}
        except requests.exceptions.Timeout:
            return {"error": "SAE request timed out", "status": "timeout"}
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else 500
            return {"error": str(e), "status_code": status}
        except Exception as e:
            logger.error("SAE error: %s", e)
            return {"error": str(e), "status": "unknown_error"}

    def _post(self, path: str, body: dict, params: Optional[dict] = None) -> dict:
        try:
            r = requests.post(f"{self.base_url}{path}", json=body, params=params, timeout=self.timeout)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.ConnectionError:
            return {"error": "SAE service unavailable", "status": "connection_error"}
        except Exception as e:
            return {"error": str(e), "status": "unknown_error"}

    def _rp(self, rules: Optional[dict]) -> dict:
        return {"rules": json.dumps(rules)} if rules else {}

    def health_check(self) -> bool:
        return self._get("/sae/health").get("status") == "ok"

    def get_student_analysis(self, student_id: str, rules: Optional[dict] = None) -> dict:
        return self._get(f"/sae/student/{student_id}", self._rp(rules))

    def get_advisor_overview(self, advisor_id: str, rules: Optional[dict] = None) -> dict:
        return self._get("/sae/advisor/overview", {"advisor_id": advisor_id, **self._rp(rules)})

    def get_advisor_analysis(self, student_id: str, rules: Optional[dict] = None) -> dict:
        return self._get(f"/sae/student/{student_id}/analysis", self._rp(rules))

    def get_course_risk(self, level: Optional[str] = None) -> dict:
        params = {"level": level} if level else None
        return self._get("/sae/courses/risk", params)

    def simulate_gpa(self, student_id: str, grades: dict, rules: Optional[dict] = None) -> dict:
        return self._post(f"/sae/student/{student_id}/simulate", {"grades": grades}, self._rp(rules))
