"""``kehrwoche-admin`` — the instance administration, inside the container.

Deliberately not part of the web interface: these are the jobs of whoever runs the
server, not of a household admin. Everything it does lives in ``app/services/admin.py``;
this file only asks, prints and sets the exit code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from app.config import get_settings
from app.db import get_session_factory
from app.services import admin as admin_service
from app.services import users as user_service
from app.services.admin import AdminError

cli = typer.Typer(help="Administration of a Kehrwoche instance.", no_args_is_help=True)
user_cli = typer.Typer(help="Accounts.", no_args_is_help=True)
household_cli = typer.Typer(help="Households.", no_args_is_help=True)
cli.add_typer(user_cli, name="user")
cli.add_typer(household_cli, name="household")


def _fail(error: AdminError) -> None:
    """One line on stderr and a non-zero exit code — no stack trace for the operator."""
    typer.secho(str(error), err=True, fg=typer.colors.RED)
    raise typer.Exit(code=1)


@user_cli.command("list")
def user_list() -> None:
    """Show every account of this instance."""
    with get_session_factory()() as db:
        rows = admin_service.list_users(db)

    if not rows:
        typer.echo("No accounts yet.")
        return

    typer.echo(f"{'ID':>4}  {'LOGIN NAME':<20} {'NAME':<20} {'HOUSEHOLD':<20} ROLE / STATE")
    for row in rows:
        state = []
        if not row.active:
            state.append("locked")
        if row.must_change_password:
            state.append("password change pending")
        suffix = f" ({', '.join(state)})" if state else ""
        typer.echo(
            f"{row.id:>4}  {row.username:<20} {row.name:<20} {row.household:<20} {row.role}{suffix}"
        )


@user_cli.command("reset-password")
def user_reset_password(username: str) -> None:
    """Set a one-time password and require a change at the next sign-in."""
    try:
        with get_session_factory()() as db:
            password = admin_service.reset_password(db, username)
    except AdminError as error:
        _fail(error)

    typer.echo(f"One-time password for {username}: {password}")
    typer.echo("It has to be changed at the next sign-in. All devices were signed out.")


@user_cli.command("lock")
def user_lock(username: str) -> None:
    """Lock an account and end all its sessions."""
    try:
        with get_session_factory()() as db:
            admin_service.set_active(db, username, active=False)
    except AdminError as error:
        _fail(error)
    typer.echo(f"{username} is locked.")


@user_cli.command("unlock")
def user_unlock(username: str) -> None:
    """Let an account sign in again."""
    try:
        with get_session_factory()() as db:
            admin_service.set_active(db, username, active=True)
    except AdminError as error:
        _fail(error)
    typer.echo(f"{username} can sign in again.")


@user_cli.command("erase")
def user_erase(
    username: Annotated[str | None, typer.Argument(help="Login name of the account.")] = None,
    every: Annotated[
        bool, typer.Option("--all", help="Every waiting account, across all households.")
    ] = False,
) -> None:
    """Finish an erasure that is waiting for a settlement.

    A deleted account keeps its name while money is open, so an unpaid claim stays
    attributable. If the household never settles, this ends the wait.
    """
    if bool(username) == every:
        # Never a default of "everybody": that would make people disappear from the books
        # of households where nobody ever objected.
        _fail(AdminError("Name an account, or use --all deliberately — not both, not neither."))
        return

    with get_session_factory()() as db:
        if username:
            user = admin_service.find_user(db, username)
            if user.erasure_requested_at is None:
                _fail(AdminError(f"No erasure is pending for {username!r}."))
                return
            waiting = [user]
        else:
            waiting = user_service.pending_erasures(db)
            if not waiting:
                typer.echo("No erasure is waiting.")
                return
            typer.echo(f"{len(waiting)} account(s) across all households are waiting.")
            typer.confirm("Complete all of them?", abort=True)

        for user in waiting:
            name = user.username
            user_service.erase_now(db, user)
            typer.echo(f"{name}: name and login name removed.")


@household_cli.command("erase")
def household_erase(household_id: int) -> None:
    """Finish the erasures waiting on one household's settlement."""
    with get_session_factory()() as db:
        waiting = user_service.pending_erasures(db, household_id=household_id)
        if not waiting:
            typer.echo("No erasure is waiting in this household.")
            return
        for user in waiting:
            name = user.username
            user_service.erase_now(db, user)
            typer.echo(f"{name}: name and login name removed.")


@household_cli.command("list")
def household_list() -> None:
    """Show every household of this instance."""
    with get_session_factory()() as db:
        rows = admin_service.list_households(db)

    if not rows:
        typer.echo("No households yet.")
        return

    typer.echo(f"{'ID':>4}  {'NAME':<30} {'TYPE':<10} MEMBERS")
    for row in rows:
        typer.echo(f"{row.id:>4}  {row.name:<30} {row.type:<10} {row.members}")


@household_cli.command("delete")
def household_delete(
    household_id: int,
    yes: Annotated[bool, typer.Option("--yes", help="Do not ask.")] = False,
) -> None:
    """Delete a household with chores, shopping list, expenses and pinboard."""
    with get_session_factory()() as db:
        rows = {row.id: row for row in admin_service.list_households(db)}
        row = rows.get(household_id)
        if row is None:
            _fail(AdminError(f"No household with the id {household_id}."))
            return

        if not yes:
            typer.echo(
                f"Household {row.id} ({row.name}) with {row.members} member(s) is about to "
                "be deleted, together with its chores, shopping list, expenses and "
                "pinboard. The accounts themselves stay."
            )
            typer.confirm("Really delete?", abort=True)

        try:
            name = admin_service.delete_household(db, household_id)
        except AdminError as error:
            _fail(error)

    typer.echo(f"Household {name!r} deleted.")


@cli.command("backup")
def backup(target: Annotated[Path, typer.Argument(help="File or directory.")]) -> None:
    """Write a consistent copy of the SQLite database while the server keeps running."""
    try:
        written = admin_service.backup_database(get_settings(), target)
    except AdminError as error:
        _fail(error)
        return
    typer.echo(f"Backup written: {written}")


def main() -> None:
    cli()


if __name__ == "__main__":  # pragma: no cover — module entry point
    main()
