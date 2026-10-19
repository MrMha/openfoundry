import asyncio
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import litellm
import pytest
from openai import AsyncOpenAI
from sqlalchemy import select

from backend.app.config import Settings
from backend.app.core.llm_router import (
    AccountingError,
    BudgetExceededError,
    ChatMessage,
    LLMRouter,
    MissionInactiveError,
    MissionNotFoundError,
    ProviderRefusalError,
    ProvidersUnavailableError,
)
from backend.app.database import Database
from backend.app.models.mission import AttemptStatus, LLMAttempt, Mission, MissionStatus

MESSAGES = [ChatMessage(role="user", content="Build a product")]
SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def response(content: str = '{"answer":42}', **overrides: Any) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10},
        **overrides,
    }


@pytest.fixture(autouse=True)
def fixed_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        litellm,
        "get_model_info",
        lambda model: {
            "max_input_tokens": 1000,
            "max_output_tokens": 4096,
            "input_cost_per_token": 0.000001,
            "output_cost_per_token": 0.000002,
        },
    )
    monkeypatch.setattr(
        litellm,
        "cost_per_token",
        lambda *, model, prompt_tokens, completion_tokens: (
            prompt_tokens * 0.000001,
            completion_tokens * 0.000002,
        ),
    )


@pytest.fixture
async def mission_id(database: Database) -> UUID:
    async with database.session() as session:
        mission = Mission(title="Test mission", goal="Build a product")
        session.add(mission)
        await session.flush()
        return mission.id


async def attempts(database: Database) -> list[LLMAttempt]:
    async with database.session() as session:
        return list(
            (await session.scalars(select(LLMAttempt).order_by(LLMAttempt.created_at))).all()
        )


async def test_success_tracks_cost_usage_and_validates_schema(
    settings: Settings, database: Database, mission_id: UUID
) -> None:
    completion = AsyncMock(return_value=response())
    router = LLMRouter(settings, database, completion=completion)
    result = await router.complete(mission_id, MESSAGES, json_schema=SCHEMA)
    assert result.structured_output == {"answer": 42}
    assert result.usage.total_tokens == 110
    assert result.cost_usd == Decimal("0.000120000")
    budget = await router.budget(mission_id)
    assert budget.spent_usd == result.cost_usd
    assert budget.reserved_usd == 0
    ledger = await attempts(database)
    assert len(ledger) == 1
    assert ledger[0].prompt_tokens == 100
    assert ledger[0].status == AttemptStatus.SUCCEEDED
    kwargs = completion.call_args.kwargs
    assert kwargs["api_key"] == "unit-test-key"
    assert kwargs["num_retries"] == kwargs["max_retries"] == 0
    assert kwargs["fallbacks"] == []
    assert kwargs["response_format"]["json_schema"]["schema"] == SCHEMA


async def test_budget_stops_provider_call(
    settings: Settings, database: Database, mission_id: UUID
) -> None:
    settings = settings.model_copy(update={"max_mission_cost_usd": Decimal("0.001")})
    completion = AsyncMock(return_value=response())
    router = LLMRouter(settings, database, completion=completion)
    with pytest.raises(BudgetExceededError):
        await router.complete(mission_id, MESSAGES)
    completion.assert_not_called()
    assert await attempts(database) == []


async def test_concurrent_routers_share_atomic_budget(
    settings: Settings, database: Database, mission_id: UUID
) -> None:
    settings = settings.model_copy(update={"max_mission_cost_usd": Decimal("0.005")})
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_completion(**kwargs: Any) -> dict[str, Any]:
        entered.set()
        await release.wait()
        return response()

    async with Database(settings) as second_database:
        first = LLMRouter(settings, database, completion=slow_completion)
        second_completion = AsyncMock(return_value=response())
        second = LLMRouter(settings, second_database, completion=second_completion)
        pending = asyncio.create_task(first.complete(mission_id, MESSAGES))
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            with pytest.raises(BudgetExceededError):
                await second.complete(mission_id, MESSAGES)
            second_completion.assert_not_called()
        finally:
            release.set()
            await pending
    assert (await first.budget(mission_id)).reserved_usd == 0


async def test_settled_spend_reduces_next_reservation(
    settings: Settings, database: Database, mission_id: UUID
) -> None:
    configured = settings.model_copy(update={"max_mission_cost_usd": Decimal("0.003048")})
    completion = AsyncMock(return_value=response())
    router = LLMRouter(configured, database, completion=completion)
    await router.complete(mission_id, MESSAGES)
    with pytest.raises(BudgetExceededError):
        await router.complete(mission_id, MESSAGES)
    assert completion.await_count == 1
    assert (await router.budget(mission_id)).spent_usd == Decimal("0.000120000")


async def test_paid_reservation_does_not_fit_falls_back_to_local(
    settings: Settings, database: Database, mission_id: UUID
) -> None:
    configured = settings.model_copy(
        update={
            "max_mission_cost_usd": Decimal("0.001"),
            "ollama_base_url": "http://localhost:11434",
        }
    )
    completion = AsyncMock(return_value=response())
    result = await LLMRouter(configured, database, completion=completion).complete(
        mission_id, MESSAGES
    )
    assert result.provider == "ollama"
    assert completion.await_count == 1


@pytest.mark.parametrize("output_tokens", [0, -1, True, 2048])
async def test_output_limits_checked_before_dispatch(
    settings: Settings, database: Database, mission_id: UUID, output_tokens: int
) -> None:
    completion = AsyncMock(return_value=response())
    with pytest.raises(ValueError):
        await LLMRouter(settings, database, completion=completion).complete(
            mission_id, MESSAGES, max_output_tokens=output_tokens
        )
    completion.assert_not_called()


async def test_external_schema_references_are_rejected_before_dispatch(
    settings: Settings, database: Database, mission_id: UUID
) -> None:
    completion = AsyncMock(return_value=response())
    with pytest.raises(ValueError, match="local references"):
        await LLMRouter(settings, database, completion=completion).complete(
            mission_id, MESSAGES, json_schema={"$ref": "https://example.com/schema"}
        )
    completion.assert_not_called()


@pytest.mark.parametrize(
    ("first", "second"),
    [("openai", "gemini"), ("gemini", "anthropic"), ("anthropic", "ollama")],
)
async def test_provider_fallback(
    settings: Settings, database: Database, mission_id: UUID, first: str, second: str
) -> None:
    configured = Settings.model_validate(
        {
            **settings.model_dump(),
            "provider_order": (first, second),
            "gemini_api_key": "unit-test-gemini",
            "anthropic_api_key": "unit-test-anthropic",
            "ollama_base_url": "http://localhost:11434",
        }
    )
    completion = AsyncMock(
        side_effect=[
            litellm.AuthenticationError(
                message="sensitive provider detail", model="test", llm_provider=first
            ),
            response(),
        ]
    )
    result = await LLMRouter(configured, database, completion=completion).complete(
        mission_id, MESSAGES
    )
    assert result.provider == second
    ledger = await attempts(database)
    assert ledger[0].status == AttemptStatus.REJECTED
    assert ledger[0].cost_usd == 0
    assert ledger[1].status == AttemptStatus.SUCCEEDED
    assert completion.call_args_list[0].kwargs["api_key"] != completion.call_args.kwargs.get(
        "api_key"
    )
    if second == "ollama":
        assert result.cost_usd == 0
        assert completion.call_args.kwargs["api_base"] == "http://localhost:11434"
        assert "api_key" not in completion.call_args.kwargs


@pytest.mark.parametrize(
    "bad_content", ['{"answer":"wrong"}', '{"answer":NaN}', '{"answer":1,"answer":2}', "not json"]
)
async def test_invalid_structured_outputs_remain_billed(
    settings: Settings, database: Database, mission_id: UUID, bad_content: str
) -> None:
    configured = settings.model_copy(update={"ollama_base_url": "http://localhost:11434"})
    completion = AsyncMock(side_effect=[response(bad_content), response()])
    router = LLMRouter(configured, database, completion=completion)
    result = await router.complete(mission_id, MESSAGES, json_schema=SCHEMA)
    assert result.provider == "ollama"
    assert (await router.budget(mission_id)).spent_usd == Decimal("0.000120000")
    assert len(await attempts(database)) == 2


async def test_timeout_retains_reservation_and_falls_back(
    settings: Settings, database: Database, mission_id: UUID
) -> None:
    configured = settings.model_copy(update={"ollama_base_url": "http://localhost:11434"})
    completion = AsyncMock(side_effect=[TimeoutError(), response()])
    router = LLMRouter(configured, database, completion=completion)
    result = await router.complete(mission_id, MESSAGES)
    assert result.provider == "ollama"
    budget = await router.budget(mission_id)
    assert budget.reserved_usd == Decimal("0.003048")
    async with Database(settings) as reopened:
        assert (
            await LLMRouter(settings, reopened).budget(mission_id)
        ).reserved_usd == budget.reserved_usd
    ledger = await attempts(database)
    assert ledger[0].status == AttemptStatus.UNCERTAIN


async def test_cancellation_retains_committed_reservation(
    settings: Settings, database: Database, mission_id: UUID
) -> None:
    entered = asyncio.Event()

    async def interrupted(**kwargs: Any) -> dict[str, Any]:
        entered.set()
        await asyncio.Event().wait()
        return response()

    router = LLMRouter(settings, database, completion=interrupted)
    pending = asyncio.create_task(router.complete(mission_id, MESSAGES))
    await asyncio.wait_for(entered.wait(), 5)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert (await router.budget(mission_id)).reserved_usd == Decimal("0.003048")


async def test_missing_usage_fails_closed(
    settings: Settings, database: Database, mission_id: UUID
) -> None:
    completion = AsyncMock(return_value=response(usage=None))
    router = LLMRouter(settings, database, completion=completion)
    with pytest.raises(AccountingError):
        await router.complete(mission_id, MESSAGES)
    assert (await router.budget(mission_id)).reserved_usd > 0


async def test_pricing_bound_violation_still_records_spend(
    settings: Settings, database: Database, mission_id: UUID
) -> None:
    completion = AsyncMock(
        return_value=response(usage={"prompt_tokens": 10_000, "completion_tokens": 10})
    )
    router = LLMRouter(settings, database, completion=completion)
    with pytest.raises(AccountingError):
        await router.complete(mission_id, MESSAGES)
    budget = await router.budget(mission_id)
    assert budget.spent_usd == Decimal("0.01002")
    assert budget.reserved_usd == 0


async def test_unknown_paid_model_is_skipped(
    settings: Settings, database: Database, mission_id: UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unknown(model: str) -> dict[str, Any]:
        raise ValueError("not in catalog")

    monkeypatch.setattr(litellm, "get_model_info", unknown)
    completion = AsyncMock(return_value=response())
    with pytest.raises(ProvidersUnavailableError):
        await LLMRouter(settings, database, completion=completion).complete(mission_id, MESSAGES)
    completion.assert_not_called()


async def test_mission_state_and_missing_credentials(
    settings: Settings, database: Database, mission_id: UUID
) -> None:
    completion = AsyncMock(return_value=response())
    router = LLMRouter(settings, database, completion=completion)
    with pytest.raises(MissionNotFoundError):
        await router.complete(uuid4(), MESSAGES)
    async with database.session() as session:
        mission = await session.get(Mission, mission_id)
        mission.status = MissionStatus.PAUSED
    with pytest.raises(MissionInactiveError):
        await router.complete(mission_id, MESSAGES)
    with pytest.raises(ProvidersUnavailableError):
        await LLMRouter(Settings(), database, completion=completion).complete(mission_id, MESSAGES)
    completion.assert_not_called()


async def test_refusal_is_billed_without_routing_around_it(
    settings: Settings, database: Database, mission_id: UUID
) -> None:
    completion = AsyncMock(
        return_value=response(
            choices=[{"message": {"refusal": "refused"}, "finish_reason": "stop"}]
        )
    )
    configured = settings.model_copy(update={"ollama_base_url": "http://localhost:11434"})
    router = LLMRouter(configured, database, completion=completion)
    with pytest.raises(ProviderRefusalError):
        await router.complete(mission_id, MESSAGES)
    assert completion.await_count == 1
    assert (await router.budget(mission_id)).spent_usd > 0


async def test_real_litellm_openai_adapter_with_mock_http(
    settings: Settings, database: Database, mission_id: UUID
) -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1,
                "model": "gpt-4o-mini",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": '{"answer":42}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http_client:
        async with AsyncOpenAI(
            api_key="unit-test-key", http_client=http_client, max_retries=0
        ) as client:

            async def completion(**kwargs: Any) -> Any:
                return await litellm.acompletion(**kwargs, client=client)

            router = LLMRouter(settings, database, completion=completion)
            result = await router.complete(mission_id, MESSAGES, json_schema=SCHEMA)
    assert result.structured_output == {"answer": 42}
    assert len(requests) == 1
    assert requests[0].url.path == "/v1/chat/completions"
