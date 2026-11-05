"""Informative polls through the API (epic E3 part A, task V1; fixture: conftest.py)."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.security import DEMO_USER_HEADER
from src.modules.governance.models import BallotRevision, Poll, PollElector
from src.modules.property.models import ApartmentMembership, User
from src.modules.property.schemas import RelationType
from tests.factories import create_membership, create_user
from tests.governance.conftest import PollWorld

API = "/api/v1/governance/polls"


def as_user(user: User) -> dict[str, str]:
    return {DEMO_USER_HEADER: str(user.id)}


def deadline(days: int = 7) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


def draft(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "title": "Spring clean-up",
        "description": "We meet in the yard at 10:00.",
        "eligibility": "RESIDENTS",
        "unit": "APARTMENT",
        "closes_at": deadline(),
        "questions": [
            {
                "text": "Which Saturday suits you?",
                "answer_mode": "MULTIPLE_CHOICE",
                "value_type": "DATE",
                "max_choices": 2,
                "options": ["2026-04-19", "2026-04-12", "2026-04-26"],
            },
            {
                "text": "How many bags will you bring?",
                "answer_mode": "OWN_VALUE",
                "value_type": "NUMBER",
                "min_value": "0",
                "max_value": "10.5",
            },
            {
                "text": "Anything else?",
                "answer_mode": "OWN_VALUE",
                "value_type": "TEXT",
                "required": False,
            },
        ],
    }
    body.update(overrides)
    return body


async def create(client: AsyncClient, author: User, **overrides: Any) -> dict:
    response = await client.post(API, json=draft(**overrides), headers=as_user(author))
    assert response.status_code == 201, response.text
    return response.json()


async def opened(client: AsyncClient, author: User, **overrides: Any) -> dict:
    poll = await create(client, author, **overrides)
    response = await client.post(f"{API}/{poll['id']}/open", headers=as_user(author))
    assert response.status_code == 200, response.text
    return response.json()


def answers_for(poll: dict, dates: list[int] | None = None, bags: str = "3") -> list[dict]:
    first, second, _ = poll["questions"]
    return [
        {
            "question_id": first["id"],
            "option_ids": [
                first["options"][i]["id"] for i in (dates if dates is not None else [0])
            ],
        },
        {"question_id": second["id"], "value": bags},
    ]


async def answer(
    client: AsyncClient, user: User, poll: dict, body: dict | None = None, **kwargs: Any
) -> Response:
    payload = body if body is not None else {"answers": answers_for(poll, **kwargs)}
    return await client.put(f"{API}/{poll['id']}/ballot", json=payload, headers=as_user(user))


async def get(client: AsyncClient, user: User, poll_id: str) -> Response:
    return await client.get(f"{API}/{poll_id}", headers=as_user(user))


# --- Drafts --------------------------------------------------------------------------------------


async def test_the_board_drafts_a_poll_with_canonical_values(
    api_client: AsyncClient, world: PollWorld
) -> None:
    for author in (world.chairman, world.board_15):
        poll = await create(api_client, author)

        assert poll["status"] == "DRAFT" and poll["kind"] == "INFORMATIVE"
        assert poll["opened_at"] is None and poll["can_answer"] is False
        dates, bags, other = poll["questions"]
        assert [option["value"] for option in dates["options"]] == [
            "2026-04-19",
            "2026-04-12",
            "2026-04-26",
        ]
        assert (dates["min_choices"], dates["max_choices"]) == (1, 2)
        assert (bags["min_value"], bags["max_value"]) == ("0", "10.5")
        assert other["required"] is False and other["options"] == []


@pytest.mark.parametrize(
    "question",
    [
        {"answer_mode": "SINGLE_CHOICE", "value_type": "TEXT", "options": ["only one"]},
        {"answer_mode": "SINGLE_CHOICE", "value_type": "NUMBER", "options": ["1", "1.0"]},
        {"answer_mode": "SINGLE_CHOICE", "value_type": "DATE", "options": ["2026-02-30", "x"]},
        {"answer_mode": "SINGLE_CHOICE", "value_type": "NUMBER", "options": ["1e3", "2"]},
        {"answer_mode": "OWN_VALUE", "value_type": "TEXT", "options": ["a", "b"]},
        {"answer_mode": "OWN_VALUE", "value_type": "TEXT", "min_value": "a"},
        {"answer_mode": "OWN_VALUE", "value_type": "NUMBER", "min_value": "5", "max_value": "1"},
        {
            "answer_mode": "SINGLE_CHOICE",
            "value_type": "TEXT",
            "options": ["a", "b"],
            "max_choices": 1,
        },
        {
            "answer_mode": "MULTIPLE_CHOICE",
            "value_type": "TEXT",
            "options": ["a", "b"],
            "min_choices": 2,
            "max_choices": 3,
        },
        {"answer_mode": "SINGLE_CHOICE", "value_type": "NUMBER", "options": [2, 3]},
    ],
)
async def test_an_inconsistent_question_is_rejected(
    api_client: AsyncClient, world: PollWorld, question: dict
) -> None:
    response = await api_client.post(
        API,
        json=draft(questions=[{"text": "Q", **question}]),
        headers=as_user(world.chairman),
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"


async def test_only_the_board_runs_polls(api_client: AsyncClient, world: PollWorld) -> None:
    poll = await create(api_client, world.chairman)
    for user in (world.owner_14, world.tenant_2, world.accountant):
        assert (await api_client.post(API, json=draft(), headers=as_user(user))).status_code == 403
        for method, path in (
            ("PUT", ""),
            ("DELETE", ""),
            ("POST", "/open"),
            ("POST", "/close"),
            ("POST", "/cancel"),
        ):
            response = await api_client.request(
                method, f"{API}/{poll['id']}{path}", json=draft(), headers=as_user(user)
            )
            assert response.status_code == 403, (user.full_name, method, path)


async def test_a_draft_is_the_boards_own(api_client: AsyncClient, world: PollWorld) -> None:
    poll = await create(api_client, world.chairman)

    assert (await get(api_client, world.board_15, poll["id"])).status_code == 200
    for user in (world.owner_14, world.tenant_2):
        assert (await get(api_client, user, poll["id"])).status_code == 404
        listed = (await api_client.get(API, headers=as_user(user))).json()
        assert listed == []


async def test_a_draft_is_replaced_and_deleted_until_it_opens(
    api_client: AsyncClient, world: PollWorld
) -> None:
    poll = await create(api_client, world.chairman)
    replaced = await api_client.put(
        f"{API}/{poll['id']}", json=draft(title="Autumn clean-up"), headers=as_user(world.chairman)
    )
    assert replaced.status_code == 200 and replaced.json()["title"] == "Autumn clean-up"
    assert replaced.json()["questions"][0]["id"] != poll["questions"][0]["id"]

    assert (
        await api_client.delete(f"{API}/{poll['id']}", headers=as_user(world.chairman))
    ).status_code == 204
    assert (await get(api_client, world.chairman, poll["id"])).status_code == 404

    live = await opened(api_client, world.chairman)
    for method in ("PUT", "DELETE"):
        response = await api_client.request(
            method, f"{API}/{live['id']}", json=draft(), headers=as_user(world.chairman)
        )
        assert response.status_code == 409
        assert response.json()["error_code"] == "POLL_NOT_DRAFT"


async def test_the_target_follows_the_rules_of_posts(
    api_client: AsyncClient, world: PollWorld
) -> None:
    for overrides in (
        {"target_scope": "ENTRANCE"},
        {"target_scope": "ENTRANCE", "target_entrance": 9},
        {"target_scope": "APARTMENT_LIST", "target_apartment_ids": [str(uuid.uuid4())]},
        {"target_scope": "ALL_BUILDING", "target_entrance": 2},
    ):
        response = await api_client.post(
            API, json=draft(**overrides), headers=as_user(world.chairman)
        )
        assert response.status_code == 422, overrides
        assert response.json()["error_code"] == "INVALID_TARGET"


# --- Opening -------------------------------------------------------------------------------------


async def test_opening_fixes_the_electorate(
    api_client: AsyncClient, db_session: AsyncSession, world: PollWorld
) -> None:
    by_apartment = await opened(api_client, world.chairman)
    by_person = await opened(api_client, world.chairman, unit="PERSON")
    owners_only = await opened(api_client, world.chairman, eligibility="OWNERS")

    async def units(poll: dict, column) -> set:
        rows = await db_session.scalars(
            select(column).where(PollElector.poll_id == uuid.UUID(poll["id"]))
        )
        return set(rows)

    assert by_apartment["status"] == "OPEN" and by_apartment["opened_at"] is not None
    assert await units(by_apartment, PollElector.apartment_id) == {
        world.apt_14.id,
        world.apt_2.id,
        world.apt_15.id,
    }
    assert await units(by_person, PollElector.user_id) == {
        world.owner_14.id,
        world.family_14.id,
        world.tenant_2.id,
        world.board_15.id,
    }
    # Apartment 2 has only a tenant: no owner can answer for it.
    assert await units(owners_only, PollElector.apartment_id) == {world.apt_14.id, world.apt_15.id}

    again = await api_client.post(
        f"{API}/{by_apartment['id']}/open", headers=as_user(world.chairman)
    )
    assert again.status_code == 409


async def test_a_poll_opens_only_with_a_future_deadline_and_someone_to_answer(
    api_client: AsyncClient, world: PollWorld
) -> None:
    late = await create(api_client, world.chairman, closes_at=deadline(-1))
    response = await api_client.post(f"{API}/{late['id']}/open", headers=as_user(world.chairman))
    assert (response.status_code, response.json()["error_code"]) == (422, "INVALID_DEADLINE")

    nobody = await create(
        api_client,
        world.chairman,
        eligibility="OWNERS",
        target_scope="APARTMENT_LIST",
        target_apartment_ids=[str(world.apt_2.id)],
    )
    response = await api_client.post(f"{API}/{nobody['id']}/open", headers=as_user(world.chairman))
    assert (response.status_code, response.json()["error_code"]) == (422, "POLL_HAS_NO_ELECTORATE")


# --- Who sees and who answers --------------------------------------------------------------------


async def test_residents_see_open_polls_of_their_target_only(
    api_client: AsyncClient, world: PollWorld
) -> None:
    entrance_2 = await opened(
        api_client, world.chairman, target_scope="ENTRANCE", target_entrance=2
    )

    assert (await get(api_client, world.owner_14, entrance_2["id"])).status_code == 200
    for outsider in (world.tenant_2, world.accountant, world.other_resident, world.other_chairman):
        assert (await get(api_client, outsider, entrance_2["id"])).status_code == 404
        assert (await answer(api_client, outsider, entrance_2)).status_code == 404
    assert (await get(api_client, world.chairman, entrance_2["id"])).status_code == 200


async def test_one_answer_per_apartment_any_member_may_change_it(
    api_client: AsyncClient, db_session: AsyncSession, world: PollWorld
) -> None:
    poll = await opened(api_client, world.chairman)

    first = await answer(api_client, world.owner_14, poll, dates=[0, 2], bags="2.50")
    assert first.status_code == 200, first.text
    (ballot,) = first.json()["my_ballots"]
    assert ballot["apartment_id"] == str(world.apt_14.id)
    assert ballot["answers"][1]["value"] == "2.5", "a canonical decimal"

    # The co-resident sees the apartment's answer, then replaces it.
    seen = (await get(api_client, world.family_14, poll["id"])).json()
    assert seen["my_ballots"][0]["answers"] == ballot["answers"]
    second = await answer(api_client, world.family_14, poll, dates=[1], bags="7")
    assert second.status_code == 200
    latest = (await get(api_client, world.owner_14, poll["id"])).json()["my_ballots"][0]
    assert latest["answers"][1]["value"] == "7"

    revisions = list(
        await db_session.scalars(select(BallotRevision.revision).order_by(BallotRevision.revision))
    )
    assert revisions == [1, 2], "every version is kept"
    assert world.owner_14.full_name not in first.text
    for secret in (str(world.owner_14.id), str(world.family_14.id), "cast_by"):
        assert secret not in second.text


async def test_one_answer_per_person(api_client: AsyncClient, world: PollWorld) -> None:
    poll = await opened(api_client, world.chairman, unit="PERSON")

    await answer(api_client, world.owner_14, poll, bags="1")
    response = await answer(api_client, world.family_14, poll, bags="2")

    assert response.status_code == 200
    assert response.json()["my_ballots"][0]["answers"][1]["value"] == "2"
    assert response.json()["my_ballots"][0]["apartment_id"] is None
    own = (await get(api_client, world.owner_14, poll["id"])).json()["my_ballots"]
    assert own[0]["answers"][1]["value"] == "1"
    with_apartment = await answer(
        api_client,
        world.owner_14,
        poll,
        body={"apartment_id": str(world.apt_14.id), "answers": answers_for(poll)},
    )
    assert with_apartment.status_code == 422


async def test_owners_only_polls_are_seen_but_not_answered_by_tenants(
    api_client: AsyncClient, world: PollWorld
) -> None:
    poll = await opened(api_client, world.chairman, eligibility="OWNERS")

    view = (await get(api_client, world.family_14, poll["id"])).json()
    assert view["can_answer"] is False and view["answerable_apartment_ids"] == []
    response = await answer(api_client, world.family_14, poll)
    assert (response.status_code, response.json()["error_code"]) == (403, "NOT_ELIGIBLE")
    assert (await answer(api_client, world.owner_14, poll)).status_code == 200


async def test_the_snapshot_decides_who_may_answer(
    api_client: AsyncClient, db_session: AsyncSession, world: PollWorld
) -> None:
    by_apartment = await opened(api_client, world.chairman)
    by_person = await opened(api_client, world.chairman, unit="PERSON")
    newcomer = await create_user(db_session, world.building, "New Tenant")
    await create_membership(db_session, newcomer, world.apt_2, relation=RelationType.TENANT)

    # An apartment in the snapshot: anyone living there now answers for it.
    assert (await answer(api_client, newcomer, by_apartment)).status_code == 200
    # One answer per person: only the people of the snapshot.
    late = await answer(api_client, newcomer, by_person)
    assert (late.status_code, late.json()["error_code"]) == (403, "NOT_ELIGIBLE")


async def test_a_person_with_two_apartments_names_the_apartment(
    api_client: AsyncClient, db_session: AsyncSession, world: PollWorld
) -> None:
    await create_membership(db_session, world.owner_14, world.apt_2)
    poll = await opened(api_client, world.chairman)

    view = (await get(api_client, world.owner_14, poll["id"])).json()
    assert set(view["answerable_apartment_ids"]) == {str(world.apt_14.id), str(world.apt_2.id)}
    unnamed = await answer(api_client, world.owner_14, poll)
    assert unnamed.status_code == 422
    for apartment in (world.apt_14, world.apt_2):
        response = await answer(
            api_client,
            world.owner_14,
            poll,
            body={"apartment_id": str(apartment.id), "answers": answers_for(poll)},
        )
        assert response.status_code == 200
    assert len((await get(api_client, world.owner_14, poll["id"])).json()["my_ballots"]) == 2
    foreign = await answer(
        api_client,
        world.owner_14,
        poll,
        body={"apartment_id": str(world.apt_15.id), "answers": answers_for(poll)},
    )
    assert foreign.json()["error_code"] == "NOT_ELIGIBLE"


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("missing_required", "REQUIRED"),
        ("unknown_option", "UNKNOWN_OPTION"),
        ("too_many_choices", "CHOICES"),
        ("no_choice", "CHOICES"),
        ("bad_number", "VALUE"),
        ("too_many_bags", "OUT_OF_RANGE"),
        ("repeated_question", "DUPLICATE"),
        ("unknown_question", "UNKNOWN_QUESTION"),
        ("value_for_choice", "CHOICES"),
    ],
)
async def test_an_answer_that_does_not_fit_names_its_question(
    api_client: AsyncClient, world: PollWorld, change: str, reason: str
) -> None:
    poll = await opened(api_client, world.chairman)
    dates, bags, _ = poll["questions"]
    answers = answers_for(poll)
    if change == "missing_required":
        answers = answers[:1]
    elif change == "unknown_option":
        answers[0]["option_ids"] = [str(uuid.uuid4())]
    elif change == "too_many_choices":
        answers[0]["option_ids"] = [option["id"] for option in dates["options"]]
    elif change == "no_choice":
        answers[0]["option_ids"] = []
    elif change == "bad_number":
        answers[1]["value"] = "three"
    elif change == "too_many_bags":
        answers[1]["value"] = "11"
    elif change == "repeated_question":
        answers.append(dict(answers[1]))
    elif change == "unknown_question":
        answers.append({"question_id": str(uuid.uuid4()), "value": "x"})
    elif change == "value_for_choice":
        answers[0] = {"question_id": dates["id"], "value": "2026-04-19"}

    response = await answer(api_client, world.owner_14, poll, body={"answers": answers})

    assert response.status_code == 422
    assert response.json()["error_code"] == "INVALID_ANSWER"
    assert response.json()["reason"] == reason


# --- Closing, extending, cancelling --------------------------------------------------------------


async def test_closing_early_ends_the_answers(api_client: AsyncClient, world: PollWorld) -> None:
    poll = await opened(api_client, world.chairman)
    await answer(api_client, world.owner_14, poll)

    closed = await api_client.post(f"{API}/{poll['id']}/close", headers=as_user(world.board_15))

    assert closed.status_code == 200
    assert closed.json()["status"] == "CLOSED" and closed.json()["closed_at"] is not None
    late = await answer(api_client, world.owner_14, poll)
    assert (late.status_code, late.json()["error_code"]) == (409, "POLL_NOT_OPEN")
    assert (await get(api_client, world.owner_14, poll["id"])).json()["my_ballots"] != []
    for path in ("/close", "/cancel"):
        response = await api_client.post(
            f"{API}/{poll['id']}{path}", headers=as_user(world.chairman)
        )
        assert response.status_code == 409


async def test_a_passed_deadline_closes_the_poll_without_a_write(
    api_client: AsyncClient, db_session: AsyncSession, world: PollWorld
) -> None:
    poll = await opened(api_client, world.chairman)
    await db_session.execute(
        update(Poll)
        .where(Poll.id == uuid.UUID(poll["id"]))
        .values(closes_at=datetime.now(UTC) - timedelta(minutes=1))
    )

    view = (await get(api_client, world.owner_14, poll["id"])).json()
    assert (view["status"], view["closed_at"], view["can_answer"]) == ("CLOSED", None, False)
    assert (await answer(api_client, world.owner_14, poll)).status_code == 409
    extend = await api_client.patch(
        f"{API}/{poll['id']}", json={"closes_at": deadline(3)}, headers=as_user(world.chairman)
    )
    assert extend.status_code == 409
    open_list = (
        await api_client.get(API, params={"status": "OPEN"}, headers=as_user(world.owner_14))
    ).json()
    assert open_list == []


async def test_the_board_extends_an_open_poll(api_client: AsyncClient, world: PollWorld) -> None:
    poll = await opened(api_client, world.chairman, closes_at=deadline(3))

    earlier = await api_client.patch(
        f"{API}/{poll['id']}", json={"closes_at": deadline(2)}, headers=as_user(world.chairman)
    )
    later = await api_client.patch(
        f"{API}/{poll['id']}", json={"closes_at": deadline(10)}, headers=as_user(world.chairman)
    )

    assert (earlier.status_code, earlier.json()["error_code"]) == (422, "INVALID_DEADLINE")
    assert later.status_code == 200
    assert later.json()["closes_at"] > poll["closes_at"]


async def test_a_cancelled_poll_shows_no_answers(api_client: AsyncClient, world: PollWorld) -> None:
    poll = await opened(api_client, world.chairman)
    await answer(api_client, world.owner_14, poll)

    cancelled = await api_client.post(f"{API}/{poll['id']}/cancel", headers=as_user(world.chairman))

    assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"
    view = (await get(api_client, world.owner_14, poll["id"])).json()
    assert (view["status"], view["my_ballots"], view["can_answer"]) == ("CANCELLED", [], False)


# --- The list ------------------------------------------------------------------------------------


async def test_the_list_shows_open_polls_first_with_the_callers_progress(
    api_client: AsyncClient, world: PollWorld
) -> None:
    soon = await opened(api_client, world.chairman, title="Soon", closes_at=deadline(2))
    later = await opened(api_client, world.chairman, title="Later", closes_at=deadline(9))
    finished = await opened(api_client, world.chairman, title="Finished")
    await api_client.post(f"{API}/{finished['id']}/close", headers=as_user(world.chairman))
    await create(api_client, world.chairman, title="Draft")
    await answer(api_client, world.owner_14, soon)

    listed = (await api_client.get(API, headers=as_user(world.family_14))).json()

    assert [(poll["title"], poll["answered"]) for poll in listed] == [
        ("Soon", True),
        ("Later", False),
        ("Finished", False),
    ]
    assert "questions" not in listed[0]
    board = (await api_client.get(API, headers=as_user(world.chairman))).json()
    assert {poll["title"] for poll in board} == {"Soon", "Later", "Finished", "Draft"}
    closed_only = (
        await api_client.get(API, params={"status": "CLOSED"}, headers=as_user(world.chairman))
    ).json()
    assert [poll["title"] for poll in closed_only] == ["Finished"]
    assert later["id"] in {poll["id"] for poll in listed}


async def test_another_building_never_sees_the_polls(
    api_client: AsyncClient, world: PollWorld
) -> None:
    poll = await opened(api_client, world.chairman)

    for user in (world.other_chairman, world.other_resident):
        assert (await api_client.get(API, headers=as_user(user))).json() == []
        assert (await get(api_client, user, poll["id"])).status_code == 404
    for path in ("/close", "/cancel", "/open"):
        response = await api_client.post(
            f"{API}/{poll['id']}{path}", headers=as_user(world.other_chairman)
        )
        assert response.status_code == 404


async def test_a_former_resident_loses_the_poll(
    api_client: AsyncClient, db_session: AsyncSession, world: PollWorld
) -> None:
    poll = await opened(api_client, world.chairman)
    await db_session.execute(
        update(ApartmentMembership)
        .where(ApartmentMembership.user_id == world.tenant_2.id)
        .values(ended_at=datetime.now(UTC))
    )

    assert (await get(api_client, world.tenant_2, poll["id"])).status_code == 404
    count = await db_session.scalar(select(func.count()).select_from(PollElector))
    assert count == 3, "the snapshot itself does not change"


# --- Review follow-ups ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target",
    [
        {"target_scope": "ALL_BUILDING"},
        {"target_scope": "ENTRANCE", "target_entrance": 1},
        {"target_scope": "ENTRANCE", "target_entrance": 2},
        {"target_scope": "APARTMENT_LIST", "apartments": ["2"]},
        {"target_scope": "APARTMENT_LIST", "apartments": ["15", "14"]},
    ],
)
async def test_who_sees_a_poll_follows_the_shared_targeting_rule(
    api_client: AsyncClient, db_session: AsyncSession, world: PollWorld, target: dict
) -> None:
    # The SQL filter of visibility and apartment_in_target (used for the electorate) agree.
    from src.modules.property.policies import apartment_in_target
    from src.modules.property.schemas import TargetScope

    two_homes = await create_user(db_session, world.building, "Two Homes")
    await create_membership(db_session, two_homes, world.apt_2)
    await create_membership(db_session, two_homes, world.apt_15)
    apartments = {"2": world.apt_2, "14": world.apt_14, "15": world.apt_15}
    overrides = {k: v for k, v in target.items() if k != "apartments"}
    if "apartments" in target:
        overrides["target_apartment_ids"] = [str(apartments[n].id) for n in target["apartments"]]
    poll = await opened(api_client, world.chairman, **overrides)
    targeted = {apartments[n].id for n in target.get("apartments", [])}

    for user, homes in (
        (world.owner_14, [world.apt_14]),
        (world.tenant_2, [world.apt_2]),
        (two_homes, [world.apt_2, world.apt_15]),
    ):
        expected = any(
            apartment_in_target(
                TargetScope(target["target_scope"]),
                target.get("target_entrance"),
                targeted,
                apartment_id=home.id,
                entrance=home.entrance,
            )
            for home in homes
        )
        seen = (await get(api_client, user, poll["id"])).status_code == 200
        assert seen is expected, (user.full_name, target)


async def test_the_list_says_who_can_answer(api_client: AsyncClient, world: PollWorld) -> None:
    owners = await opened(api_client, world.chairman, title="Owners", eligibility="OWNERS")

    (tenant_view,) = (await api_client.get(API, headers=as_user(world.family_14))).json()
    (owner_view,) = (await api_client.get(API, headers=as_user(world.owner_14))).json()

    assert (tenant_view["can_answer"], tenant_view["answered"]) == (False, False)
    assert tenant_view["answerable_apartment_ids"] == []
    assert (owner_view["can_answer"], owner_view["answerable_apartment_ids"]) == (
        True,
        [str(world.apt_14.id)],
    )
    assert owners["id"] == owner_view["id"]


async def test_a_cancelled_poll_is_not_answered_in_the_list(
    api_client: AsyncClient, world: PollWorld
) -> None:
    poll = await opened(api_client, world.chairman)
    await answer(api_client, world.owner_14, poll)
    await api_client.post(f"{API}/{poll['id']}/cancel", headers=as_user(world.chairman))

    (listed,) = (await api_client.get(API, headers=as_user(world.owner_14))).json()

    assert (listed["status"], listed["answered"], listed["can_answer"]) == (
        "CANCELLED",
        False,
        False,
    )
    assert listed["answerable_apartment_ids"] == []


async def test_the_board_filters_drafts_and_cancelled_polls(
    api_client: AsyncClient, world: PollWorld
) -> None:
    first = await create(api_client, world.chairman, title="First draft")
    second = await create(api_client, world.chairman, title="Second draft")
    gone = await opened(api_client, world.chairman, title="Gone")
    await api_client.post(f"{API}/{gone['id']}/cancel", headers=as_user(world.chairman))

    async def titles(status: str) -> list[str]:
        response = await api_client.get(
            API, params={"status": status}, headers=as_user(world.chairman)
        )
        return [poll["title"] for poll in response.json()]

    assert await titles("DRAFT") == ["Second draft", "First draft"]
    assert await titles("CANCELLED") == ["Gone"]
    assert first["id"] != second["id"]


async def test_an_ended_membership_cannot_answer_for_its_apartment(
    api_client: AsyncClient, db_session: AsyncSession, world: PollWorld
) -> None:
    second_home = await create_membership(db_session, world.owner_14, world.apt_2)
    poll = await opened(api_client, world.chairman)
    await db_session.execute(
        update(ApartmentMembership)
        .where(ApartmentMembership.id == second_home.id)
        .values(ended_at=datetime.now(UTC))
    )

    response = await answer(
        api_client,
        world.owner_14,
        poll,
        body={"apartment_id": str(world.apt_2.id), "answers": answers_for(poll)},
    )

    assert (response.status_code, response.json()["error_code"]) == (403, "NOT_ELIGIBLE")
    own = await answer(api_client, world.owner_14, poll)
    assert own.status_code == 200, "the remaining apartment needs no apartment_id"


async def test_the_same_answer_twice_is_one_version(
    api_client: AsyncClient, db_session: AsyncSession, world: PollWorld
) -> None:
    poll = await opened(api_client, world.chairman)

    for _ in range(3):
        assert (await answer(api_client, world.owner_14, poll, bags="3.0")).status_code == 200

    count = await db_session.scalar(select(func.count()).select_from(BallotRevision))
    assert count == 1


async def test_a_deadline_is_at_most_a_year_away_and_returned_in_utc(
    api_client: AsyncClient, world: PollWorld
) -> None:
    far = await create(api_client, world.chairman, closes_at=deadline(400))
    response = await api_client.post(f"{API}/{far['id']}/open", headers=as_user(world.chairman))
    assert (response.status_code, response.json()["error_code"]) == (422, "INVALID_DEADLINE")

    tallinn = (datetime.now(UTC) + timedelta(days=5)).astimezone(
        __import__("zoneinfo").ZoneInfo("Europe/Tallinn")
    )
    poll = await opened(api_client, world.chairman, closes_at=tallinn.isoformat())
    assert poll["closes_at"].endswith("Z")
    too_far = await api_client.patch(
        f"{API}/{poll['id']}", json={"closes_at": deadline(400)}, headers=as_user(world.chairman)
    )
    assert (too_far.status_code, too_far.json()["error_code"]) == (422, "INVALID_DEADLINE")


async def test_residents_cannot_extend(api_client: AsyncClient, world: PollWorld) -> None:
    poll = await opened(api_client, world.chairman)

    response = await api_client.patch(
        f"{API}/{poll['id']}", json={"closes_at": deadline(9)}, headers=as_user(world.owner_14)
    )

    assert response.status_code == 403


@pytest.mark.parametrize("closes_at", ["9999-12-31T23:00:00-05:00", "0001-01-01T00:30:00+05:00"])
async def test_a_deadline_at_the_edge_of_the_calendar_is_a_validation_error(
    api_client: AsyncClient, world: PollWorld, closes_at: str
) -> None:
    created = await api_client.post(
        API, json=draft(closes_at=closes_at), headers=as_user(world.owner_14)
    )
    poll = await opened(api_client, world.chairman)
    extended = await api_client.patch(
        f"{API}/{poll['id']}", json={"closes_at": closes_at}, headers=as_user(world.chairman)
    )

    for response in (created, extended):
        assert response.status_code == 422
        assert response.json()["error_code"] == "VALIDATION_ERROR"


async def test_another_member_confirming_the_same_answers_is_recorded(
    api_client: AsyncClient, db_session: AsyncSession, world: PollWorld
) -> None:
    poll = await opened(api_client, world.chairman)

    await answer(api_client, world.owner_14, poll, bags="3")
    await answer(api_client, world.family_14, poll, bags="3")

    count = await db_session.scalar(select(func.count()).select_from(BallotRevision))
    assert count == 2
