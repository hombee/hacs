"""Persist invitations and issue revocable credentials for individual HA users."""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta

from homeassistant.auth.const import GROUP_ID_ADMIN, GROUP_ID_USER
from homeassistant.auth.models import TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN
from homeassistant.components import person
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

INVITATION_LIFETIME = 7 * 24 * 60 * 60
CLIENT_ID = "https://hombee.app"


class InvitationError(ValueError):
    """An invitation could not be authorized or completed."""


class HombeeInvitations:
    """HA owns user identity, invitation claims and credential revocation."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.store = Store(hass, 1, f"{DOMAIN}.invitations")
        self.items: dict[str, dict] = {}
        self.lock = asyncio.Lock()

    async def load(self) -> None:
        """Load the authoritative invitation ledger."""
        self.items = await self.store.async_load() or {}

    async def save(self) -> None:
        """Persist a claim before performing external side effects."""
        await self.store.async_save(self.items)

    def snapshot(self) -> list[dict]:
        """Return management metadata without tokens or Hombee account IDs."""
        return [
            {
                **{
                    key: item[key] for key in ("inviteId", "email", "name", "expiresAt")
                },
                "userId": item.get("userId"),
                "status": (
                    "revoked"
                    if item.get("revoked")
                    else (
                        "accepted"
                        if item.get("complete")
                        else (
                            "expired" if item["expiresAt"] <= time.time() else "pending"
                        )
                    )
                ),
            }
            for item in self.items.values()
        ]

    async def issue(self, administrator_id: str, data: dict) -> dict:
        """Create an idempotent invitation with an explicit HA user binding."""
        async with self.lock:
            invite_id = data["inviteId"]
            existing = self.items.get(invite_id)
            payload = {
                "inviteId": invite_id,
                "email": data["email"].strip().lower(),
                "name": data["name"].strip(),
                "targetUserId": data.get("userId"),
                "administratorId": administrator_id,
                "isAdmin": data["isAdmin"],
            }
            if existing is not None:
                if any(existing[key] != value for key, value in payload.items()):
                    raise InvitationError("Invitation request changed.")
                return existing
            if len(self.items) >= 200:
                raise InvitationError("The invitation limit has been reached.")
            if payload["targetUserId"] is not None:
                user = await self.hass.auth.async_get_user(payload["targetUserId"])
                if (
                    user is None
                    or not user.is_active
                    or user.is_owner
                    or user.system_generated
                    or user.local_only
                ):
                    raise InvitationError("Choose an active, remotely accessible user.")
                # Existing users retain the permissions already assigned in HA.
            self.items[invite_id] = {
                **payload,
                "expiresAt": int(time.time()) + INVITATION_LIFETIME,
            }
            await self.save()
            return self.items[invite_id]

    async def accept(self, administrator_id: str, data: dict) -> dict:
        """Claim once and return the invited user's own renewable credentials."""
        async with self.lock:
            item = self.items.get(data["inviteId"])
            if (
                item is None
                or item.get("revoked")
                or item["expiresAt"] <= time.time()
                or item["administratorId"] != administrator_id
                or item["email"] != data["email"].strip().lower()
            ):
                raise InvitationError("The invitation is unavailable.")
            uid = data["hombeeUserId"]
            if item.get("hombeeUserId", uid) != uid:
                raise InvitationError("This invitation belongs to another account.")
            user_id = item.get("userId", item["targetUserId"])
            if user_id and any(
                other.get("userId") == user_id
                and other.get("hombeeUserId") not in (None, uid)
                and not other.get("revoked")
                for other in self.items.values()
            ):
                raise InvitationError(
                    "This HA user is associated with another account."
                )
            item["hombeeUserId"] = uid
            await self.save()
            if user_id is None:
                user = await self.hass.auth.async_create_user(
                    item["name"],
                    group_ids=[GROUP_ID_ADMIN if item["isAdmin"] else GROUP_ID_USER],
                    local_only=False,
                )
                item["userId"] = user.id
                await self.save()
            else:
                user = await self.hass.auth.async_get_user(user_id)
                if (
                    user is None
                    or not user.is_active
                    or user.is_owner
                    or user.system_generated
                    or user.local_only
                ):
                    raise InvitationError("The invited HA user is unavailable.")
                item["userId"] = user.id

            # Preserve existing person names and tracking configuration.
            collections = self.hass.data[person.DOMAIN][:2]
            if not any(
                entry.get(person.ATTR_USER_ID) == user.id
                for collection in collections
                for entry in collection.async_items()
            ):
                await person.async_create_person(
                    self.hass, item["name"], user_id=user.id
                )

            refresh = user.refresh_tokens.get(item.get("refreshTokenId"))
            long_lived = user.refresh_tokens.get(item.get("longLivedTokenId"))
            if item.get("complete") and (refresh is None or long_lived is None):
                raise InvitationError("Access was revoked. Ask for a new invitation.")
            if refresh is None:
                refresh = await self.hass.auth.async_create_refresh_token(
                    user, client_id=CLIENT_ID, client_name="Hombee"
                )
                item["refreshTokenId"] = refresh.id
                await self.save()
            if long_lived is None:
                long_lived = await self.hass.auth.async_create_refresh_token(
                    user,
                    client_name=f"Hombee invitation {item['inviteId']}",
                    token_type=TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN,
                    access_token_expiration=timedelta(days=3650),
                )
                item["longLivedTokenId"] = long_lived.id
                await self.save()
            access_token = self.hass.auth.async_create_access_token(refresh)
            long_lived_token = self.hass.auth.async_create_access_token(long_lived)
            item["complete"] = True
            await self.save()
            return {
                "userId": user.id,
                "accessToken": access_token,
                "refreshToken": refresh.token,
                "longLivedAccessToken": long_lived_token,
                "expiresIn": int(refresh.access_token_expiration.total_seconds()),
                "oauthClientId": CLIENT_ID,
            }

    async def revoke(self, invite_id: str) -> None:
        """Revoke this association without deleting the person's HA user."""
        async with self.lock:
            item = self.items.get(invite_id)
            if item is None:
                raise InvitationError("Unknown invitation.")
            item["revoked"] = True
            await self.save()
            user = await self.hass.auth.async_get_user(item.get("userId", ""))
            if user is not None:
                for key in ("refreshTokenId", "longLivedTokenId"):
                    if token := user.refresh_tokens.get(item.get(key)):
                        self.hass.auth.async_remove_refresh_token(token)
