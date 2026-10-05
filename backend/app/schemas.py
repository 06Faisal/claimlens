"""Pydantic models. Request limits live here: this is the trust boundary."""
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

Money = Decimal  # constrained per-field below


def money(**kw):
    return Field(ge=0, le=Decimal("100000000"), max_digits=12, decimal_places=2, allow_inf_nan=False, **kw)


class AssessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    policy_id: str = Field(pattern=r"^[a-z0-9-]{1,40}$")
    description: str = Field(min_length=3, max_length=2000)
    policy_start_date: date  # latest issue/renewal date: selects which IRDAI rule versions apply
    claim_date: date
    # Uploaded policy text, held by the browser (stateless server). Only with policy_id "upload-...".
    policy_chunks: Optional[list[Annotated[str, Field(min_length=1, max_length=2000)]]] = Field(default=None, max_length=400)
    pre_existing: Optional[bool] = None  # user-stated; overrides what the model infers from the text
    continuous_coverage_months: int = Field(ge=0, le=600)
    room_rent_per_day: Decimal = money()
    room_days: int = Field(ge=0, le=365)
    associated_charges: Decimal = money()  # nursing, consultation, surgeon, OT
    other_charges: Decimal = money()  # pharmacy, implants, diagnostics, consumables
    non_payable_charges: Decimal = money()  # items the policy never covers

    @model_validator(mode="after")
    def _dates(self):
        if self.policy_chunks is not None and not self.policy_id.startswith("upload-"):
            raise ValueError("policy_chunks need an upload- policy_id")
        if self.claim_date < self.policy_start_date:
            raise ValueError("claim_date before policy_start_date")
        if self.claim_date - self.policy_start_date > timedelta(days=366 * 50):
            raise ValueError("date range too large")
        return self


class Cited(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: Decimal = Field(ge=0, le=Decimal("100000000"), allow_inf_nan=False)
    clause_id: str = Field(max_length=120)


class Exclusion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    term: str = Field(min_length=4, max_length=80)
    clause_id: str = Field(max_length=120)


class SubLimit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    procedure: str = Field(min_length=2, max_length=80)
    amount: Decimal = Field(ge=0, le=Decimal("100000000"), allow_inf_nan=False)
    clause_id: str = Field(max_length=120)


class Rules(BaseModel):
    """Rule parameters extracted from retrieved clauses. Every value carries its source clause."""
    model_config = ConfigDict(extra="forbid")
    sum_insured: Cited
    room_rent_cap_per_day: Optional[Cited] = None
    copay_percent: Optional[Cited] = Field(default=None)
    deductible: Optional[Cited] = None
    waiting_period_months: Optional[Cited] = None  # waiting period applicable to this diagnosis
    ped_waiting_months: Optional[Cited] = None
    irdai_ped_cap_months: Optional[Cited] = None
    exclusion: Optional[Exclusion] = None
    sub_limit: Optional[SubLimit] = None
    unsupported: list[Literal["restoration_benefit", "package_rate", "other"]] = []

    @model_validator(mode="after")
    def _pct(self):
        if self.copay_percent and self.copay_percent.value > 100:
            raise ValueError("copay > 100")
        return self


class Step(BaseModel):
    name: str
    amount_after: Decimal
    note: str
    clause_ids: list[str] = []


class Outcome(BaseModel):
    verdict: Literal["payable", "partially_payable", "rejected"]
    payable: Decimal
    claimed: Decimal
    steps: list[Step]
    clause_ids: list[str]


class Assessment(BaseModel):
    status: Literal["answered", "abstained"]
    verdict: Optional[str] = None
    payable: Optional[Decimal] = None
    claimed: Optional[Decimal] = None
    steps: list[Step] = []
    explanation: Optional[str] = None
    citations: list[dict] = []  # {clause_id, title, text}
    reason: Optional[str] = None  # abstain reason
    disclaimer: str = "Informational estimate only. Not legal or financial advice; the insurer's decision and your policy wording prevail."
