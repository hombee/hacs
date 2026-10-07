"""Validate invitation identity, real HA permissions and token revocation."""

import asyncio
from unittest.mock import patch

import pytest
from homeassistant.components import person
from homeassistant.helpers import instance_id
from homeassistant.setup import async_setup_component

from custom_components.hombee.invitation_api import async_setup_invitations
from custom_components.hombee.invitations import HombeeInvitations, InvitationError

INVITE_ID = "a4b702de-71d1-4c0c-8df1-c6e6d0e9f402"


@pytest.fixture
async def ledger(hass, hass_admin_user):
    """Use real HA users, persons, persisted storage and token validation."""
    assert await async_setup_component(hass, "person", {})
    runtime = HombeeInvitations(hass)
    await runtime.load()
    await runtime.issue(
        hass_admin_user.id,
        {
            "inviteId": INVITE_ID,
            "email": "guest@example.com",
            "name": "Guest",
            "isAdmin": False,
        },
    )
    return runtime


def claim(uid="hombee-guest"):
    """The backend authenticates the Hombee account and supplies the invite email."""
    return {"inviteId": INVITE_ID, "email": "guest@example.com", "hombeeUserId": uid}


async def test_accept_issues_guest_credentials_and_person(
    hass, hass_admin_user, ledger
):
    """The connection authenticates as the guest, never as the inviter."""
    grant = await ledger.accept(hass_admin_user.id, claim())
    token = hass.auth.async_validate_access_token(grant["accessToken"])
    assert token is not None
    assert token.user.id == grant["userId"]
    assert token.user.id != hass_admin_user.id
    assert not token.user.is_admin
    persons = hass.data[person.DOMAIN][1].async_items()
    assert len(persons) == 1
    assert persons[0]["user_id"] == token.user.id
    assert ledger.snapshot()[0]["status"] == "accepted"
    assert "refreshToken" not in ledger.snapshot()[0]


async def test_same_account_retry_survives_reload(hass, hass_admin_user, ledger):
    """Lost responses and reloads retain one user, person and credential grant."""
    first = await ledger.accept(hass_admin_user.id, claim())
    reloaded = HombeeInvitations(hass)
    await reloaded.load()
    second = await reloaded.accept(hass_admin_user.id, claim())
    assert first["userId"] == second["userId"]
    assert first["refreshToken"] == second["refreshToken"]
    assert len(hass.data[person.DOMAIN][1].async_items()) == 1
    assert len((await hass.auth.async_get_user(first["userId"])).refresh_tokens) == 2


async def test_concurrent_accounts_cannot_share_invitation(hass_admin_user, ledger):
    """The ledger serializes claims and rejects another Hombee identity."""
    results = await asyncio.gather(
        ledger.accept(hass_admin_user.id, claim("one")),
        ledger.accept(hass_admin_user.id, claim("two")),
        return_exceptions=True,
    )
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(isinstance(result, InvitationError) for result in results) == 1


async def test_revoke_invalidates_both_tokens(hass, hass_admin_user, ledger):
    """Revocation disables every credential issued by this invitation."""
    grant = await ledger.accept(hass_admin_user.id, claim())
    await ledger.revoke(INVITE_ID)
    assert hass.auth.async_validate_access_token(grant["accessToken"]) is None
    assert hass.auth.async_validate_access_token(grant["longLivedAccessToken"]) is None
    assert (await hass.auth.async_get_user(grant["userId"])).is_active
    with pytest.raises(InvitationError):
        await ledger.accept(hass_admin_user.id, claim())


async def test_expired_invitation_cannot_create_user(hass, hass_admin_user, ledger):
    """Expired links fail before provisioning identities."""
    users = await hass.auth.async_get_users()
    with (
        patch(
            "custom_components.hombee.invitations.time.time",
            return_value=ledger.items[INVITE_ID]["expiresAt"],
        ),
        pytest.raises(InvitationError),
    ):
        await ledger.accept(hass_admin_user.id, claim())
    assert await hass.auth.async_get_users() == users


async def test_existing_person_and_permissions_are_preserved(
    hass, hass_admin_user, ledger
):
    """Explicit user selection links the account without changing that user's role."""
    user = await hass.auth.async_create_user("Existing", group_ids=["system-read-only"])
    await person.async_create_person(hass, "Resident", user_id=user.id)
    invite_id = "b4b702de-71d1-4c0c-8df1-c6e6d0e9f402"
    await ledger.issue(
        hass_admin_user.id,
        {
            "inviteId": invite_id,
            "email": "other@example.com",
            "name": "Existing",
            "userId": user.id,
            "isAdmin": True,
        },
    )
    grant = await ledger.accept(
        hass_admin_user.id,
        {"inviteId": invite_id, "email": "other@example.com", "hombeeUserId": "other"},
    )
    assert grant["userId"] == user.id
    assert not user.is_admin
    assert user.groups[0].id == "system-read-only"
    persons = hass.data[person.DOMAIN][1].async_items()
    assert len(persons) == 1
    assert persons[0]["name"] == "Resident"


async def test_admin_api_requires_current_admin_and_matching_instance(
    hass, hass_ws_client, hass_read_only_access_token
):
    """Ordinary users cannot issue invitations or mint credentials."""
    assert await async_setup_component(hass, "person", {})
    await async_setup_invitations(hass)
    reader = await hass_ws_client(hass, access_token=hass_read_only_access_token)
    await reader.send_json({"id": 1, "type": "hombee/invitations/status"})
    assert not (await reader.receive_json())["success"]
    admin = await hass_ws_client(hass)
    await admin.send_json(
        {
            "id": 1,
            "type": "hombee/invitations/issue",
            "instanceId": "wrong",
            "inviteId": INVITE_ID,
            "email": "guest@example.com",
            "name": "Guest",
            "isAdmin": False,
        }
    )
    response = await admin.receive_json()
    assert response["error"]["code"] == "wrong_instance"
    await admin.send_json(
        {
            "id": 2,
            "type": "hombee/invitations/issue",
            "instanceId": await instance_id.async_get(hass),
            "inviteId": INVITE_ID,
            "email": "guest@example.com",
            "name": "Guest",
            "isAdmin": False,
        }
    )
    assert (await admin.receive_json())["success"]
