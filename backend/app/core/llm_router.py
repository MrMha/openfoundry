"""Credential-isolated LiteLLM routing with durable, conservative mission budgets.

Only non-streaming text chat is exposed: tools, images, implicit SDK retries, and other
unbounded charge sources are intentionally outside this API. Dollar amounts are
list-price estimates from LiteLLM's pricing catalog, not provider invoice guarantees.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, Decimal
from typing import Any, Literal, cast
from uuid import UUID, uuid4

import litellm
from jsonschema import Draft202012Validator, ValidationError
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from pydantic import ValidationError as PydanticValidationError
from referencing import Registry
from sqlalchemy import literal, update

from backend.app.config import USD_QUANTUM, Provider, Settings
from backend.app.database import Database
from backend.app.models.mission import USD, AttemptStatus, LLMAttempt, Mission, MissionStatus

logger = logging.getLogger(__name__)
JSONSchema = dict[str, Any]
CompletionFunction = Callable[..., Awaitable[Any]]


class LLMRouterError(RuntimeError):
    """Base class for safe, prompt-free router errors."""


class MissionNotFoundError(LLMRouterError):
    """The mission does not exist."""


class MissionInactiveError(LLMRouterError):
    """The mission is not allowed to issue new requests."""


class BudgetExceededError(LLMRouterError):
    """No configured route fits the remaining budget."""


class AccountingError(LLMRouterError):
    """A reservation was retained because billing could not safely be reconciled."""


class PricingUnavailableError(LLMRouterError):
    """A paid model has no trustworthy bounded pricing metadata."""


class ProvidersUnavailableError(LLMRouterError):
    """All configured providers failed, with no raw provider errors exposed."""


class StructuredOutputError(LLMRouterError):
    """The provider returned incomplete text or text that does not match the schema."""


class ProviderRefusalError(LLMRouterError):
    """The provider refused the request; do not route around a safety refusal."""


class ChatMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class TokenUsage:
    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True, slots=True)
class LLMResult:
    mission_id: UUID
    attempt_id: UUID
    provider: Provider
    model: str
    content: str
    usage: TokenUsage
    cost_usd: Decimal
    structured_output: JsonValue = None


@dataclass(frozen=True, slots=True)
class BudgetSnapshot:
    spent_usd: Decimal
    reserved_usd: Decimal
    ceiling_usd: Decimal

    @property
    def remaining_usd(self) -> Decimal:
        return max(Decimal(0), self.ceiling_usd - self.spent_usd - self.reserved_usd)


@dataclass(frozen=True, slots=True)
class _Route:
    provider: Provider
    model: str
    api_key: str | None = field(repr=False)
    api_base: str | None = None


@dataclass(frozen=True, slots=True)
class _Bound:
    cost_usd: Decimal
    max_input_tokens: int
    max_output_tokens: int


class _Usage(BaseModel):
    prompt_tokens: int = Field(ge=0, strict=True)
    completion_tokens: int = Field(ge=0, strict=True)


class _Message(BaseModel):
    content: str | None = None
    refusal: str | None = None


class _Choice(BaseModel):
    message: _Message
    finish_reason: str | None = None


class _Response(BaseModel):
    choices: list[_Choice] = Field(min_length=1, max_length=1)
    usage: _Usage


def _usd(value: Decimal) -> Decimal:
    if not value.is_finite() or value < 0:
        raise AccountingError("Pricing returned a negative or non-finite cost")
    return value.quantize(USD_QUANTUM, rounding=ROUND_CEILING)


def _strict_json(text: str) -> JsonValue:
    def reject_constant(value: str) -> Any:
        raise ValueError("Non-finite JSON number")

    def unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def finite_float(value: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("JSON number exceeds floating point range")
        return number

    return cast(
        JsonValue,
        json.loads(
            text,
            parse_constant=reject_constant,
            parse_float=finite_float,
            object_pairs_hook=unique_keys,
        ),
    )


class LLMRouter:
    """One configured user and a shared database; safe across tasks and worker processes.

    Reserve against the model's entire input window plus bounded output before each
    network call. This deliberately sacrifices some utilization to avoid trusting local
    token estimates as an exact upper bound. Unknown paid models are skipped. Timeouts,
    cancellations, and process crashes retain reservations for manual invoice reconciliation.
    Provider SDK retries and LiteLLM fallbacks are disabled so every request has a ledger row.
    """

    def __init__(
        self,
        settings: Settings,
        database: Database,
        *,
        completion: CompletionFunction | None = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self._completion: CompletionFunction = completion or litellm.acompletion

    def _routes(self) -> list[_Route]:
        routes: list[_Route] = []
        for provider in self.settings.provider_order:
            if provider == "ollama":
                if self.settings.ollama_base_url:
                    routes.append(
                        _Route(
                            provider,
                            self.settings.ollama_model,
                            None,
                            self.settings.ollama_base_url,
                        )
                    )
            else:
                secret = getattr(self.settings, f"{provider}_api_key")
                if secret is not None:
                    routes.append(
                        _Route(
                            provider,
                            getattr(self.settings, f"{provider}_model"),
                            secret.get_secret_value(),
                        )
                    )
        return routes

    def _bound(self, route: _Route, output_tokens: int) -> _Bound:
        if route.provider == "ollama":
            # Local infrastructure/energy charges are outside the API-token dollar budget.
            return _Bound(Decimal(0), 0, output_tokens)
        try:
            info: Mapping[str, Any] = litellm.get_model_info(route.model)
            max_input = int(info["max_input_tokens"])
            max_output = int(info["max_output_tokens"])
            if max_input <= 0 or max_output <= 0:
                raise ValueError("Missing context limits")
            rates: list[Decimal] = []
            for direction in ("input", "output"):
                # Use the highest published tier, including long-context prices.
                candidates = [
                    Decimal(str(value))
                    for key, value in info.items()
                    if key.startswith(f"{direction}_cost_per_token") and value is not None
                ]
                if not candidates or any(not rate.is_finite() or rate < 0 for rate in candidates):
                    raise ValueError("Invalid model pricing")
                rates.append(max(candidates))
            if rates[0] <= 0 or rates[1] <= 0:
                raise ValueError("Paid models require positive token prices")
            limited_output = min(output_tokens, max_output)
            bound = _usd(rates[0] * max_input + rates[1] * limited_output)
            return _Bound(bound, max_input, limited_output)
        except Exception:
            raise PricingUnavailableError(
                "No bounded pricing available for configured model"
            ) from None

    async def budget(self, mission_id: UUID | str) -> BudgetSnapshot:
        async with self.database.session() as session:
            mission = await session.get(Mission, UUID(str(mission_id)))
            if mission is None:
                raise MissionNotFoundError("Mission does not exist")
            return BudgetSnapshot(
                mission.total_cost_usd,
                mission.reserved_cost_usd,
                self.settings.max_mission_cost_usd,
            )

    async def _reserve(self, mission_id: UUID, route: _Route, bound: _Bound) -> UUID:
        attempt_id = uuid4()
        async with self.database.session() as session:
            reserved = await session.scalar(
                update(Mission)
                .where(
                    Mission.id == mission_id,
                    Mission.status.in_([MissionStatus.PENDING, MissionStatus.RUNNING]),
                    Mission.total_cost_usd + Mission.reserved_cost_usd
                    <= literal(
                        self.settings.max_mission_cost_usd - bound.cost_usd,
                        type_=USD(),
                    ),
                )
                .values(reserved_cost_usd=Mission.reserved_cost_usd + bound.cost_usd)
                .returning(Mission.id)
            )
            if reserved is None:
                mission = await session.get(Mission, mission_id)
                if mission is None:
                    raise MissionNotFoundError("Mission does not exist")
                if mission.status not in {MissionStatus.PENDING, MissionStatus.RUNNING}:
                    raise MissionInactiveError("Mission must be pending or running")
                raise BudgetExceededError("Request exceeds the available mission budget")
            session.add(
                LLMAttempt(
                    id=attempt_id,
                    mission_id=mission_id,
                    provider=route.provider,
                    model=route.model,
                    reserved_cost_usd=bound.cost_usd,
                )
            )
        return attempt_id

    async def _uncertain(self, attempt_id: UUID, error_code: str) -> None:
        async with self.database.session() as session:
            await session.execute(
                update(LLMAttempt)
                .where(LLMAttempt.id == attempt_id, LLMAttempt.status == AttemptStatus.RESERVED)
                .values(status=AttemptStatus.UNCERTAIN, error_code=error_code)
            )

    async def _settle(
        self,
        attempt_id: UUID,
        cost: Decimal,
        usage: TokenUsage,
        *,
        rejected: bool = False,
        error_code: str | None = None,
    ) -> None:
        async with self.database.session() as session:
            # Claim settlement first using one write, avoiding a read/upgrade race in SQLite.
            row = (
                await session.execute(
                    update(LLMAttempt)
                    .where(
                        LLMAttempt.id == attempt_id,
                        LLMAttempt.status == AttemptStatus.RESERVED,
                    )
                    .values(
                        status=AttemptStatus.REJECTED if rejected else AttemptStatus.SUCCEEDED,
                        cost_usd=cost,
                        prompt_tokens=usage.prompt_tokens,
                        completion_tokens=usage.completion_tokens,
                        error_code=error_code,
                    )
                    .returning(LLMAttempt.mission_id, LLMAttempt.reserved_cost_usd)
                )
            ).one_or_none()
            if row is None:
                raise AccountingError("LLM attempt is missing or was already settled")
            mission_id, reservation = row
            await session.execute(
                update(Mission)
                .where(Mission.id == mission_id)
                .values(
                    total_cost_usd=Mission.total_cost_usd + cost,
                    reserved_cost_usd=Mission.reserved_cost_usd - reservation,
                )
            )

    async def _dispatch(
        self,
        mission_id: UUID,
        route: _Route,
        bound: _Bound,
        kwargs: dict[str, Any],
        max_attempts: int,
    ) -> tuple[UUID, Any, int] | None:
        """Ledger every physical call; retain ambiguous reservations across bounded retries."""
        for attempt in range(max_attempts):
            attempt_id = await self._reserve(mission_id, route, bound)
            try:
                async with asyncio.timeout(self.settings.request_timeout_seconds):
                    return attempt_id, await self._completion(**kwargs), attempt + 1
            except asyncio.CancelledError:
                raise  # The committed reservation survives cancellation.
            except litellm.ContentPolicyViolationError:
                await self._uncertain(attempt_id, "provider_refusal")
                raise ProviderRefusalError("Provider refused the request") from None
            except (
                litellm.AuthenticationError,
                litellm.PermissionDeniedError,
                litellm.BadRequestError,
                litellm.NotFoundError,
                litellm.UnsupportedParamsError,
            ) as error:
                status = getattr(error, "status_code", None)
                code = (
                    f"http_{status}" if status in {400, 401, 403, 404, 422} else "request_rejected"
                )
                await self._settle(
                    attempt_id, Decimal(0), TokenUsage(0, 0), rejected=True, error_code=code
                )
                logger.warning("LLM route rejected: provider=%s reason=%s", route.provider, code)
                return None
            except (
                litellm.APIError,
                litellm.ServiceUnavailableError,
                litellm.RateLimitError,
                TimeoutError,
                ConnectionError,
            ) as error:
                status = getattr(error, "status_code", None)
                code = (
                    f"http_{status}"
                    if status in {408, 429, 500, 502, 503, 504}
                    else "provider_timeout"
                    if isinstance(error, (TimeoutError, litellm.Timeout))
                    else "provider_failure"
                )
                await self._uncertain(attempt_id, code)
                logger.warning(
                    "LLM route failed: provider=%s reason=%s reservation_retained",
                    route.provider,
                    code,
                )
                # Never replay an ambiguous timeout automatically. Explicit overload/rate
                # responses may retry, with a new reservation and no provider/key rotation.
                if status not in {429, 503} or attempt + 1 == max_attempts:
                    return None
                delay = min(30.0, self.settings.provider_retry_delay_seconds * 2**attempt)
                response = getattr(error, "response", None)
                retry_after = response.headers.get("retry-after") if response is not None else None
                if retry_after:
                    try:
                        requested_delay = float(retry_after)
                    except ValueError:
                        return None  # A date-valued hint needs a later operator retry.
                    if not math.isfinite(requested_delay) or requested_delay > 30:
                        return None
                    delay = max(delay, requested_delay)
                await asyncio.sleep(delay)
            except Exception:
                await self._uncertain(attempt_id, "unexpected_failure")
                raise LLMRouterError("Unexpected provider failure; reservation retained") from None
        return None

    @staticmethod
    def _schema_validator(schema: JSONSchema | None) -> Draft202012Validator | None:
        if schema is None:
            return None
        Draft202012Validator.check_schema(schema)

        def check_references(value: Any) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in {"$ref", "$dynamicRef"} and (
                        not isinstance(child, str) or not child.startswith("#")
                    ):
                        raise ValueError("JSON schemas must use local references")
                    check_references(child)
            elif isinstance(value, list):
                for child in value:
                    check_references(child)

        check_references(schema)
        # Empty registry: local $defs references work, external references never fetch URLs.
        return Draft202012Validator(schema, registry=Registry())

    async def complete(
        self,
        mission_id: UUID | str,
        messages: Sequence[ChatMessage],
        *,
        json_schema: JSONSchema | None = None,
        output_validator: Callable[[JsonValue], object] | None = None,
        max_output_tokens: int | None = None,
        temperature: float = 0.2,
    ) -> LLMResult:
        mission_uuid = UUID(str(mission_id))
        if not messages:
            raise ValueError("At least one chat message is required")
        if not math.isfinite(temperature) or not 0 <= temperature <= 2:
            raise ValueError("Temperature must be finite and between zero and two")
        output_tokens = (
            self.settings.max_output_tokens if max_output_tokens is None else max_output_tokens
        )
        if (
            isinstance(output_tokens, bool)
            or not isinstance(output_tokens, int)
            or output_tokens <= 0
        ):
            raise ValueError("max_output_tokens must be a positive integer")
        if output_tokens > self.settings.max_output_tokens:
            raise ValueError("max_output_tokens exceeds the configured output limit")
        # Snapshot a caller's mutable schema before any await or provider library can mutate it.
        if json_schema is not None:
            json_schema = json.loads(json.dumps(json_schema, allow_nan=False))
        validator = self._schema_validator(json_schema)
        routes = self._routes()
        if not routes:
            raise ProvidersUnavailableError("Configure an API key or OLLAMA_BASE_URL")
        budget_failures = 0
        requests_made: dict[Provider, int] = {}
        feedback: dict[Provider, list[dict[str, str]]] = {}
        while routes:
            route = routes.pop(0)
            try:
                bound = await asyncio.to_thread(self._bound, route, output_tokens)
            except PricingUnavailableError:
                logger.warning("LLM route skipped: provider=%s reason=pricing", route.provider)
                continue
            kwargs: dict[str, Any] = {
                "model": route.model,
                "messages": [message.model_dump() for message in messages],
                "max_tokens": bound.max_output_tokens,
                "temperature": temperature,
                "timeout": self.settings.request_timeout_seconds,
                "num_retries": 0,
                "max_retries": 0,
                "fallbacks": [],
                "stream": False,
                "n": 1,
                "caching": False,
            }
            if route.api_key is not None:
                kwargs["api_key"] = route.api_key
            if route.api_base is not None:
                kwargs["api_base"] = route.api_base
            if json_schema is not None:
                if route.provider == "gemini":
                    # Gemini rejects some valid Pydantic schema constructs at dispatch.
                    # JSON mode plus the complete schema avoids remote grammar limits;
                    # the original Draft202012Validator below remains authoritative.
                    kwargs["response_format"] = {"type": "json_object"}
                    kwargs["messages"] = [
                        {
                            "role": "system",
                            "content": "Return a JSON object matching this JSON Schema exactly. "
                            "Include every required field and no extra fields.\n"
                            + json.dumps(json_schema, separators=(",", ":")),
                        },
                        *kwargs["messages"],
                    ]
                else:
                    kwargs["response_format"] = {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "mission_output",
                            "strict": True,
                            "schema": json_schema,
                        },
                    }
            if route.provider in feedback:
                kwargs["messages"].extend(feedback[route.provider])
            try:
                dispatched = await self._dispatch(
                    mission_uuid,
                    route,
                    bound,
                    kwargs,
                    self.settings.provider_max_attempts - requests_made.get(route.provider, 0),
                )
            except BudgetExceededError:
                budget_failures += 1
                continue
            if dispatched is None:
                continue
            attempt_id, raw_response, physical_attempts = dispatched
            requests_made[route.provider] = requests_made.get(route.provider, 0) + physical_attempts

            try:
                payload = (
                    raw_response.model_dump()
                    if isinstance(raw_response, BaseModel)
                    else raw_response
                )
                response = _Response.model_validate(payload)
                usage = TokenUsage(response.usage.prompt_tokens, response.usage.completion_tokens)
                if route.provider == "ollama":
                    cost = Decimal(0)
                else:
                    input_cost, output_cost = litellm.cost_per_token(
                        model=route.model,
                        prompt_tokens=usage.prompt_tokens,
                        completion_tokens=usage.completion_tokens,
                    )
                    cost = _usd(Decimal(str(input_cost)) + Decimal(str(output_cost)))
            except Exception:
                await self._uncertain(attempt_id, "missing_or_invalid_usage")
                raise AccountingError(
                    "Could not price provider usage; reservation retained"
                ) from None

            # Persist spend even when output is invalid, refused, or exceeds catalog bounds.
            await self._settle(attempt_id, cost, usage)
            if cost > bound.cost_usd or (
                route.provider != "ollama"
                and (
                    usage.prompt_tokens > bound.max_input_tokens
                    or usage.completion_tokens > bound.max_output_tokens
                )
            ):
                raise AccountingError(
                    "Provider exceeded reserved pricing or token bounds; spend recorded"
                )
            choice = response.choices[0]
            if choice.message.refusal or choice.finish_reason == "content_filter":
                raise ProviderRefusalError("Provider refused the request")
            try:
                if choice.finish_reason != "stop" or not choice.message.content:
                    raise StructuredOutputError("Provider returned empty or incomplete output")
                content = choice.message.content
                structured_output: JsonValue = None
                if validator is not None:
                    try:
                        structured_output = _strict_json(content)
                        validator.validate(structured_output)
                    except (ValueError, ValidationError) as error:
                        detail = (
                            f"Schema rule {error.validator!r} failed. "
                            f"Path: {list(error.absolute_path)}."
                            f" Expected: {error.validator_value!r}"
                            if isinstance(error, ValidationError)
                            else "Return valid, unambiguous JSON."
                        )
                        raise StructuredOutputError(
                            "Response did not match the JSON schema. " + detail
                        ) from None
                    if output_validator is not None:
                        try:
                            output_validator(structured_output)
                        except (ValueError, TypeError, SyntaxError) as error:
                            detail = (
                                json.dumps(
                                    error.errors(
                                        include_input=False,
                                        include_context=False,
                                        include_url=False,
                                    ),
                                    default=str,
                                )[:2000]
                                if isinstance(error, PydanticValidationError)
                                else f"Python syntax error: {error.msg} at line {error.lineno}"
                                if isinstance(error, SyntaxError)
                                else type(error).__name__
                            )
                            raise StructuredOutputError(
                                "Response failed application validation: " + detail
                            ) from None
            except StructuredOutputError as error:
                logger.warning("LLM output invalid: provider=%s spend_recorded", route.provider)
                async with self.database.session() as session:
                    await session.execute(
                        update(LLMAttempt)
                        .where(LLMAttempt.id == attempt_id)
                        .values(error_code="invalid_output")
                    )
                if requests_made[route.provider] < self.settings.provider_max_attempts:
                    feedback[route.provider] = []
                    if choice.message.content:
                        feedback[route.provider].append(
                            {
                                "role": "assistant",
                                "content": choice.message.content[:64000],
                            }
                        )
                    feedback[route.provider].append(
                        {
                            "role": "user",
                            "content": str(error)
                            + " Correct the invalid response above. Return a complete "
                            "JSON object matching the supplied schema and task requirements; "
                            "include all required fields. Keep it concise enough for "
                            "the output limit.",
                        }
                    )
                    routes.insert(0, route)
                continue
            return LLMResult(
                mission_uuid,
                attempt_id,
                route.provider,
                route.model,
                content,
                usage,
                cost,
                structured_output,
            )
        if budget_failures:
            raise BudgetExceededError("No usable provider fits the remaining mission budget")
        raise ProvidersUnavailableError(
            "All configured LLM providers failed or returned invalid output"
        )
