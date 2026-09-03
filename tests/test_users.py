"""Profile, pictures and giving up an account."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import AppError, ErrorCode
from app.images import MAX_EDGE, process_image, resolve_image, store_image
from app.main import API_PREFIX
from app.models import FeedEvent, User
from tests.test_auth import CREDENTIALS
from tests.test_household import HOUSEHOLD, sign_up


def picture(size: tuple[int, int] = (64, 48), colour: str = "red", **save: object) -> bytes:
    """A real picture, built in memory."""
    buffer = BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="PNG", **save)
    return buffer.getvalue()


def portrait_jpeg() -> bytes:
    """A landscape image that a phone marked as "turn me upright"."""
    exif = Image.Exif()
    exif[0x0112] = 6  # orientation: rotate 90° clockwise
    buffer = BytesIO()
    Image.new("RGB", (60, 20), "blue").save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


@pytest.fixture
async def founder(client: AsyncClient) -> AsyncClient:
    await sign_up(client)
    await client.post(f"{API_PREFIX}/household", json=HOUSEHOLD)
    return client


# --- The picture pipeline (unit) ------------------------------------------------------


def test_a_file_that_only_claims_to_be_a_picture_is_refused() -> None:
    """The content decides, never the file name — that is the whole point."""
    with pytest.raises(AppError) as error:
        process_image(b"#!/bin/sh\necho not a picture\n")

    assert error.value.status_code == 400
    assert error.value.field == "file"


def test_an_empty_upload_is_refused() -> None:
    with pytest.raises(AppError):
        process_image(b"")


def test_a_picture_is_re_encoded_as_webp() -> None:
    processed = process_image(picture())

    with Image.open(BytesIO(processed)) as result:
        assert result.format == "WEBP"


def test_a_large_picture_is_scaled_down() -> None:
    processed = process_image(picture((2000, 1000)))

    with Image.open(BytesIO(processed)) as result:
        assert max(result.size) == MAX_EDGE
        assert result.size == (MAX_EDGE, MAX_EDGE // 2), "the aspect ratio survives"


def test_a_portrait_photo_arrives_upright() -> None:
    """Acceptance case: a photo taken sideways must not stay sideways."""
    processed = process_image(portrait_jpeg())

    with Image.open(BytesIO(processed)) as result:
        assert result.size == (20, 60), "60x20 rotated by the EXIF flag"


def test_the_same_picture_is_stored_once(tmp_path: Path) -> None:
    first = store_image(picture(), tmp_path)
    second = store_image(picture(), tmp_path)

    assert first == second
    assert len(list(tmp_path.glob("*.webp"))) == 1
    assert list(tmp_path.glob("*.part")) == [], "no half-written file is left behind"


def test_different_pictures_get_different_names(tmp_path: Path) -> None:
    assert store_image(picture(colour="red"), tmp_path) != store_image(
        picture(colour="green"), tmp_path
    )


@pytest.mark.parametrize("name", ["../../etc/passwd", "not-a-hash.webp", "abc.png", "a" * 64])
def test_only_our_own_file_names_are_resolved(tmp_path: Path, name: str) -> None:
    with pytest.raises(AppError) as error:
        resolve_image(name, tmp_path)

    assert error.value.status_code == 404


# --- The profile ----------------------------------------------------------------------


async def test_the_profile_can_be_changed(founder: AsyncClient, db_session: Session) -> None:
    response = await founder.patch(
        f"{API_PREFIX}/me",
        json={"first_name": " Alexandra ", "last_name": "Meier", "locale": "en"},
    )

    assert response.status_code == 200
    assert response.json()["first_name"] == "Alexandra"
    assert response.json()["last_name"] == "Meier"
    assert response.json()["locale"] == "en"


async def test_an_email_address_can_be_added_and_removed(founder: AsyncClient) -> None:
    added = await founder.patch(f"{API_PREFIX}/me", json={"email": " Alex@Example.org "})
    assert added.json()["email"] == "alex@example.org", "trimmed and lowercased"

    removed = await founder.patch(f"{API_PREFIX}/me", json={"email": ""})
    assert removed.json()["email"] is None


async def test_an_address_somebody_else_uses_is_refused(
    founder: AsyncClient, db_session: Session
) -> None:
    other = db_session.scalar(select(User))
    assert other is not None
    other.email = "taken@example.org"
    db_session.commit()
    await founder.post(f"{API_PREFIX}/auth/logout")
    await sign_up(founder, "bea")

    response = await founder.patch(f"{API_PREFIX}/me", json={"email": "taken@example.org"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == ErrorCode.EMAIL_TAKEN


async def test_an_empty_name_is_refused(founder: AsyncClient) -> None:
    response = await founder.patch(f"{API_PREFIX}/me", json={"first_name": "   "})

    assert response.status_code == 400
    assert response.json()["error"]["field"] == "first_name"


async def test_the_profile_needs_a_sign_in(client: AsyncClient) -> None:
    await client.get(f"{API_PREFIX}/meta")

    assert (await client.patch(f"{API_PREFIX}/me", json={"locale": "en"})).status_code == 401


# --- Avatar and household picture -----------------------------------------------------


async def test_an_avatar_is_stored_and_served(founder: AsyncClient, settings: Settings) -> None:
    response = await founder.post(
        f"{API_PREFIX}/me/avatar", files={"file": ("photo.png", picture(), "image/png")}
    )

    assert response.status_code == 200
    name = response.json()["avatar_file"]
    assert name.endswith(".webp")
    assert (settings.media_dir / name).is_file()

    served = await founder.get(f"/media/{name}")
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/webp"
    assert "immutable" in served.headers["cache-control"]


async def test_a_disguised_file_is_refused_as_an_avatar(founder: AsyncClient) -> None:
    """Acceptance case: not an image, however the upload labels itself."""
    response = await founder.post(
        f"{API_PREFIX}/me/avatar",
        files={"file": ("avatar.png", b"MZ\x90\x00 definitely not a picture", "image/png")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["field"] == "file"


async def test_replacing_an_avatar_removes_the_old_file(
    founder: AsyncClient, settings: Settings
) -> None:
    first = (
        await founder.post(
            f"{API_PREFIX}/me/avatar", files={"file": ("a.png", picture(colour="red"), "image/png")}
        )
    ).json()["avatar_file"]

    await founder.post(
        f"{API_PREFIX}/me/avatar", files={"file": ("b.png", picture(colour="green"), "image/png")}
    )

    assert not (settings.media_dir / first).exists()


async def test_a_picture_two_people_share_survives(
    app, founder: AsyncClient, settings: Settings, db_session: Session
) -> None:
    """Pictures are stored by content — deleting one account cannot blank the other."""
    from httpx import ASGITransport

    from tests.conftest import CsrfAwareClient

    same_picture = picture(colour="blue")
    await founder.post(
        f"{API_PREFIX}/me/avatar", files={"file": ("a.png", same_picture, "image/png")}
    )
    join_code = (await founder.get(f"{API_PREFIX}/household")).json()["join_code"]

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with CsrfAwareClient(transport=transport, base_url="https://testserver") as second:
        await second.get(f"{API_PREFIX}/meta")
        await sign_up(second, "bea")
        await second.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
        name = (
            await second.post(
                f"{API_PREFIX}/me/avatar", files={"file": ("b.png", same_picture, "image/png")}
            )
        ).json()["avatar_file"]
        await second.request(
            "DELETE", f"{API_PREFIX}/me", json={"password": CREDENTIALS["password"]}
        )

    assert (settings.media_dir / name).is_file(), "the founder still uses it"


async def test_only_admins_change_the_household_picture(
    app, founder: AsyncClient, settings: Settings
) -> None:
    from httpx import ASGITransport

    from tests.conftest import CsrfAwareClient

    join_code = (await founder.get(f"{API_PREFIX}/household")).json()["join_code"]
    mine = await founder.post(
        f"{API_PREFIX}/household/image", files={"file": ("h.png", picture(), "image/png")}
    )
    assert mine.status_code == 200
    assert mine.json()["image_file"].endswith(".webp")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with CsrfAwareClient(transport=transport, base_url="https://testserver") as member:
        await member.get(f"{API_PREFIX}/meta")
        await sign_up(member, "bea")
        await member.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})

        response = await member.post(
            f"{API_PREFIX}/household/image", files={"file": ("h.png", picture(), "image/png")}
        )

    assert response.status_code == 403


async def test_pictures_are_not_public(app, founder: AsyncClient) -> None:
    from httpx import ASGITransport

    from tests.conftest import CsrfAwareClient

    name = (
        await founder.post(
            f"{API_PREFIX}/me/avatar", files={"file": ("a.png", picture(), "image/png")}
        )
    ).json()["avatar_file"]

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with CsrfAwareClient(transport=transport, base_url="https://testserver") as stranger:
        response = await stranger.get(f"/media/{name}")

    assert response.status_code == 401


async def test_an_unknown_picture_is_not_found(founder: AsyncClient) -> None:
    """A name that is not one of our hashes never reaches the file system."""
    assert (await founder.get(f"/media/{'0' * 64}.webp")).status_code == 404
    assert (await founder.get("/media/etc-passwd.webp")).status_code == 404


# --- Deleting the account -------------------------------------------------------------


async def test_deleting_needs_the_right_password(founder: AsyncClient) -> None:
    response = await founder.request(
        "DELETE", f"{API_PREFIX}/me", json={"password": "definitely-wrong"}
    )

    assert response.status_code == 403
    assert response.json()["error"]["field"] == "password"


async def test_a_deleted_account_is_anonymised_and_locked(
    app, founder: AsyncClient, settings: Settings, db_session: Session
) -> None:
    """Acceptance case: the history stays, the person does not, and nobody signs in."""
    from httpx import ASGITransport

    from tests.conftest import CsrfAwareClient

    # Somebody has to stay behind — the last member takes the household with them.
    join_code = (await founder.get(f"{API_PREFIX}/household")).json()["join_code"]
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with CsrfAwareClient(transport=transport, base_url="https://testserver") as second:
        await second.get(f"{API_PREFIX}/meta")
        await sign_up(second, "bea")
        await second.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})

    # Deleting includes leaving, so the admin role has to be passed on first — the same
    # rule that protects a household from losing its last admin.
    bea = db_session.scalar(select(User).where(User.username == "bea"))
    assert bea is not None
    await founder.patch(f"{API_PREFIX}/household/members/{bea.id}", json={"role": "admin"})

    await founder.post(f"{API_PREFIX}/chores", json={"title": "Bad", "rotation_seconds": 604800})
    avatar = (
        await founder.post(
            f"{API_PREFIX}/me/avatar", files={"file": ("a.png", picture(), "image/png")}
        )
    ).json()["avatar_file"]

    response = await founder.request(
        "DELETE", f"{API_PREFIX}/me", json={"password": CREDENTIALS["password"]}
    )

    assert response.status_code == 204
    user = db_session.scalar(select(User).order_by(User.id))
    assert user is not None
    assert user.first_name == ""
    assert user.email is None
    assert user.username.startswith("deleted-")
    assert user.avatar_file is None
    assert user.is_active is False
    assert not (settings.media_dir / avatar).exists()

    # The feed keeps its entries — they are the audit log of the household.
    assert db_session.scalars(select(FeedEvent)).all() != []

    # And the account is gone for good: no session, no sign-in.
    assert (await founder.get(f"{API_PREFIX}/me")).status_code == 401
    login = await founder.post(f"{API_PREFIX}/auth/login", json=CREDENTIALS)
    assert login.status_code in (401, 403)


async def test_deleting_needs_a_sign_in(client: AsyncClient) -> None:
    await client.get(f"{API_PREFIX}/meta")

    response = await client.request("DELETE", f"{API_PREFIX}/me", json={"password": "x"})

    assert response.status_code == 401


async def test_the_last_admin_has_to_pass_the_role_on_first(app, founder: AsyncClient) -> None:
    """Deleting includes moving out — a household must not lose its last admin."""
    from httpx import ASGITransport

    from tests.conftest import CsrfAwareClient

    join_code = (await founder.get(f"{API_PREFIX}/household")).json()["join_code"]
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with CsrfAwareClient(transport=transport, base_url="https://testserver") as second:
        await second.get(f"{API_PREFIX}/meta")
        await sign_up(second, "bea")
        await second.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})

    response = await founder.request(
        "DELETE", f"{API_PREFIX}/me", json={"password": CREDENTIALS["password"]}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == ErrorCode.LAST_ADMIN


# --- Erasure that waits for the settlement --------------------------------------------


async def test_a_departed_member_keeps_their_name_while_money_is_open(
    app, founder: AsyncClient, settings: Settings, db_session: Session
) -> None:
    """One rule, three views: balances, settlement and archive have to agree."""
    from httpx import ASGITransport

    from tests.conftest import CsrfAwareClient

    join_code = (await founder.get(f"{API_PREFIX}/household")).json()["join_code"]
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with CsrfAwareClient(transport=transport, base_url="https://testserver") as second:
        await second.get(f"{API_PREFIX}/meta")
        await sign_up(second, "bea")
        await second.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
        # Alex pays for both, so Bea owes something when she moves out.
        await founder.post(
            f"{API_PREFIX}/expenses", json={"title": "Einkauf", "amount_cents": 2000}
        )
        await second.post(f"{API_PREFIX}/household/leave")

    bea = db_session.scalar(select(User).where(User.username == "bea"))
    assert bea is not None

    balances = (await founder.get(f"{API_PREFIX}/expenses/balances")).json()
    settlement = (await founder.get(f"{API_PREFIX}/expenses/settlement")).json()
    named = {row["user_id"]: row["first_name"] for row in balances}
    assert named[bea.id] == "Alex", "the name the account still carries"
    assert [entry["user_id"] for entry in settlement["names"]] == [bea.id]

    # Archiving settles the period — and with it the reason to show the name.
    await founder.post(f"{API_PREFIX}/expenses/archive")

    after = (await founder.get(f"{API_PREFIX}/expenses/balances")).json()
    # Settled and gone from the open period — there is nothing left to attribute.
    assert bea.id not in {row["user_id"] for row in after}
    periods = (await founder.get(f"{API_PREFIX}/expenses/periods")).json()
    detail = (await founder.get(f"{API_PREFIX}/expenses/periods/{periods[0]['id']}")).json()
    assert detail["names"] == [], "nothing left to justify a name"


async def test_deleting_with_an_open_balance_keeps_only_the_name(
    app, founder: AsyncClient, settings: Settings, db_session: Session
) -> None:
    """Access ends at once; the name waits for the settlement, nothing else does."""
    from httpx import ASGITransport

    from tests.conftest import CsrfAwareClient

    join_code = (await founder.get(f"{API_PREFIX}/household")).json()["join_code"]
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with CsrfAwareClient(transport=transport, base_url="https://testserver") as second:
        await second.get(f"{API_PREFIX}/meta")
        await sign_up(second, "bea")
        await second.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
        await second.patch(f"{API_PREFIX}/me", json={"email": "bea@example.org"})
        await second.post(
            f"{API_PREFIX}/me/avatar", files={"file": ("a.png", picture(), "image/png")}
        )
        await founder.post(
            f"{API_PREFIX}/expenses", json={"title": "Einkauf", "amount_cents": 2000}
        )

        deleted = await second.request(
            "DELETE", f"{API_PREFIX}/me", json={"password": CREDENTIALS["password"]}
        )
        assert deleted.status_code == 204
        # Whatever else happens, the door is shut.
        assert (await second.get(f"{API_PREFIX}/me")).status_code == 401

    bea = db_session.scalar(select(User).where(User.first_name == "Alex", User.id != 1))
    assert bea is not None
    assert bea.erasure_requested_at is not None, "the erasure is marked as pending"
    assert bea.username == "bea", "name and login name wait for the settlement"
    assert bea.email is None
    assert bea.avatar_file is None
    assert bea.is_active is False

    # Settling releases the erasure.
    await founder.post(f"{API_PREFIX}/expenses/archive")

    db_session.expire_all()
    bea = db_session.get(User, bea.id)
    assert bea is not None
    assert bea.first_name == ""
    assert bea.username.startswith("deleted-")
    assert bea.erasure_requested_at is None


async def test_the_scheduler_finishes_an_erasure_when_the_balance_is_corrected(
    app, founder: AsyncClient, db_session: Session
) -> None:
    """Not every settlement is an archive: a deleted expense clears a balance too."""
    from httpx import ASGITransport

    from app.services.users import finish_pending_erasures
    from tests.conftest import CsrfAwareClient

    join_code = (await founder.get(f"{API_PREFIX}/household")).json()["join_code"]
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with CsrfAwareClient(transport=transport, base_url="https://testserver") as second:
        await second.get(f"{API_PREFIX}/meta")
        await sign_up(second, "bea")
        await second.post(f"{API_PREFIX}/household/join", json={"join_code": join_code})
        expense = (
            await founder.post(
                f"{API_PREFIX}/expenses", json={"title": "Einkauf", "amount_cents": 2000}
            )
        ).json()
        await second.request(
            "DELETE", f"{API_PREFIX}/me", json={"password": CREDENTIALS["password"]}
        )

    assert finish_pending_erasures(db_session) == 0, "still open, nothing to do"

    await founder.delete(f"{API_PREFIX}/expenses/{expense['id']}")
    db_session.expire_all()

    assert finish_pending_erasures(db_session) == 1
    remaining = db_session.scalars(select(User).where(User.erasure_requested_at.is_not(None))).all()
    assert remaining == []
