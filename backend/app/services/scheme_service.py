# backend/app/services/scheme_service.py

from typing import Any, Dict


class SchemeEligibilityEngine:
    @staticmethod
    def check_pmjay_rural(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        In-depth PM-JAY (Ayushman Bharat) Eligibility logic for Rural areas
        Based on SECC 2011 deprivation criteria D1 to D7.
        """
        reasons = []
        is_eligible = False
        confidence = 0.5

        # 1. Automatic inclusion categories
        occupation = str(data.get("occupation", "")).lower()
        if any(
            keyword in occupation
            for keyword in ["manual scavenger", "scavenger", "destitute", "beggar", "bonded labor"]
        ):
            is_eligible = True
            reasons.append("Automatic Inclusion: Vulnerable occupational group")
            confidence = 0.9

        # 2. SECC Deprivation Criteria
        # D1: Kucha House
        if str(data.get("housing_type", "")).lower() in ["kucha", "mud", "thatch"]:
            is_eligible = True
            reasons.append("D1: Living in kucha walls and kucha roof")
            confidence = 0.8

        # D4: SC/ST
        caste = str(data.get("caste_category", "")).lower()
        if any(c in caste for c in ["sc", "st", "scheduled"]):
            is_eligible = True
            reasons.append("D4: SC/ST household member identified")
            confidence = 0.8

        # D5: Landless & Manual Labour
        if "labor" in occupation or "labour" in occupation:
            is_eligible = True
            reasons.append("D5: Landless household deriving income from casual manual labour")
            confidence = 0.8

        # 3. Proxy Indicators (Ration Cards)
        ration_card = str(data.get("ration_card_type", "")).lower().strip()
        if ration_card and ration_card not in ["no", "none", "not present", "nil", "nahi", "नहीं"]:
            is_eligible = True
            reasons.append("Proxy Inclusion: Ration card holder")
            confidence = 0.9

        # Age based state schemes (e.g. Old age pension/medical aid)
        age_val = 0
        try:
            age_val = int(data.get("age", 0))
        except (TypeError, ValueError):
            pass

        is_state_eligible = age_val >= 60 or is_eligible
        state_reasons = []
        if age_val >= 60:
            state_reasons.append("Senior Citizen medical aid eligibility")

        return {
            "pmjay": {"eligible": is_eligible, "reasons": reasons, "confidence": confidence},
            "state_scheme": {
                "eligible": is_state_eligible,
                "reasons": state_reasons + reasons if not state_reasons and is_eligible else state_reasons,
            },
        }
