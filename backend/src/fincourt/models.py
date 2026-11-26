from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, localcontext
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints, model_validator

SHANGHAI = ZoneInfo("Asia/Shanghai")
CalendarDate = date
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")]
MoneyText = Annotated[str, StringConstraints(pattern=r"^-?[0-9]{1,24}(?:\.[0-9]{1,12})?$")]
SourceKind = Literal["REAL", "SIMULATED"]
RunStatus = Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]
Decision = Literal["SUPPORTED", "REFUTED", "DISPUTED", "INSUFFICIENT"]
VersionStatus = Literal["CURRENT", "SUPERSEDED"]
AuditStatus = Literal["AUDITED", "UNAUDITED", "UNKNOWN"]
Scope = Literal["CONSOLIDATED", "PARENT", "UNKNOWN"]
Unit = Literal["yuan", "wan_yuan", "yi_yuan", "shares", "wan_shares", "ratio", "percent"]
UNITS = {
    "yuan": ("money", Decimal("1")),
    "wan_yuan": ("money", Decimal("10000")),
    "yi_yuan": ("money", Decimal("100000000")),
    "shares": ("shares", Decimal("1")),
    "wan_shares": ("shares", Decimal("10000")),
    "ratio": ("ratio", Decimal("1")),
    "percent": ("ratio", Decimal("0.01")),
}


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Period(Input):
    start: date
    end: date

    @model_validator(mode="after")
    def ordered(self):
        if self.start > self.end:
            raise ValueError("报告期开始日期晚于结束日期")
        return self


class Cutoff(Input):
    mode: Literal["PUBLIC_END_OF_DAY", "PUBLIC_INSTANT"] = "PUBLIC_END_OF_DAY"
    date: CalendarDate | None = None
    at: datetime | None = None
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"

    @model_validator(mode="after")
    def exactly_one_boundary(self):
        if self.mode == "PUBLIC_END_OF_DAY":
            if self.date is None or self.at is not None or self.date == date.max:
                raise ValueError("日终截止需要有效日期")
        elif self.date is not None or self.at is None or self.at.utcoffset() is None:
            raise ValueError("精确截止需要含时区的时间")
        return self

    def includes(self, published_date: str, published_at: str | None) -> bool:
        day = date.fromisoformat(published_date)
        if self.mode == "PUBLIC_END_OF_DAY":
            return day <= self.date
        if published_at:
            return datetime.fromisoformat(published_at) <= self.at
        return datetime.combine(day + timedelta(days=1), time.min, SHANGHAI) <= self.at


class Amount(Input):
    value: MoneyText
    unit: Unit
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")] | None = None

    @model_validator(mode="after")
    def check_currency(self):
        if (UNITS[self.unit][0] == "money") != (self.currency is not None):
            raise ValueError("金额必须标明币种，其他维度不能指定币种")
        return self

    def normalized(self) -> tuple[Decimal, str, str | None]:
        dimension, scale = UNITS[self.unit]
        with localcontext() as context:
            context.prec = 64
            return Decimal(self.value) * scale, dimension, self.currency


class FactCandidate(Amount):
    id: Identifier
    metric: Identifier
    period: Period
    scope: Scope
    audit_status: AuditStatus = "UNKNOWN"
    page: Annotated[int, Field(ge=1, le=300)]
    quote: Annotated[str, Field(min_length=1, max_length=1600)]
    replaces: Annotated[list[Identifier], Field(max_length=32)] = Field(default_factory=list)


class DocumentInput(Input):
    id: Identifier
    title: Annotated[str, Field(min_length=1, max_length=240)]
    subject: Identifier
    source_kind: SourceKind
    source_url: HttpUrl | None = None
    source_cluster: Annotated[str, Field(min_length=1, max_length=160)] | None = None
    disclosure_type: Literal["RESULTS", "CORRECTION", "UPDATE", "AUDIT", "COMMENTARY", "AGREEMENT"]
    published_date: date
    published_at: datetime | None = None
    facts: Annotated[list[FactCandidate], Field(max_length=100)] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_disclosure(self):
        if self.published_date == date.max:
            raise ValueError("披露日期超出范围")
        if self.published_at is not None and (
            self.published_at.utcoffset() is None
            or self.published_at.astimezone(SHANGHAI).date() != self.published_date
        ):
            raise ValueError("披露时间必须带时区，并与上海时区披露日期一致")
        if len({item.id for item in self.facts}) != len(self.facts):
            raise ValueError("候选事实 ID 重复")
        if self.disclosure_type != "CORRECTION" and any(item.replaces for item in self.facts):
            raise ValueError("仅更正公告可以替代已披露字段")
        return self


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
