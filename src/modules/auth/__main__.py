"""Operator commands of module auth (docs/API.md section 7, "First chairman").

    uv run python -m src.modules.auth create-chairman --email chair@example.com [--force]
        [--print-link]

It creates a STAFF invitation with CHAIRMAN on behalf of the system and sends it. It refuses when
the building already has an active chairman, unless --force (the only chairman lost their
mailbox). Without mail it warns; --print-link prints the link to this terminal, nowhere else.
"""

import argparse
import asyncio
import sys
from collections.abc import Callable
from typing import TextIO

from pydantic import TypeAdapter, ValidationError

from src.core.config import Settings, get_settings
from src.core.domain_events import SessionFactory
from src.modules.auth.invitations import InvitationService
from src.modules.auth.mail import mail_allowed, send_auth_mails
from src.modules.auth.policies import validate_auth_settings
from src.modules.auth.schemas import EmailAddress
from src.modules.property.service import PropertyService
from src.plugins.registry import PluginRegistry, build_plugin_registry


async def create_chairman(
    session_factory: SessionFactory,
    settings: Settings,
    plugins: PluginRegistry,
    *,
    email: str,
    force: bool,
    print_link: bool,
    out: TextIO,
) -> int:
    """Returns the exit code."""
    try:
        canonical = TypeAdapter(EmailAddress).validate_python(email)
    except ValidationError:
        print("error: --email is not an e-mail address", file=out)
        return 2
    async with session_factory() as session:
        properties = PropertyService(session)
        buildings = await properties.building_ids()
        if len(buildings) != 1:
            print(f"error: expected one building, found {len(buildings)}", file=out)
            return 1
        building_id = buildings[0]
        if await properties.active_chairman_count(building_id) and not force:
            print("error: the building has an active chairman; use --force to recover", file=out)
            return 1
        invitation_id, mail = await InvitationService(session, settings).invite_first_chairman(
            building_id, canonical
        )
        await session.commit()
    print(f"invitation {invitation_id} created", file=out)
    can_mail = plugins.channel("email") is not None and mail_allowed(settings, canonical)
    if can_mail:
        await send_auth_mails(plugins, settings, [mail])
        print("invitation e-mail sent", file=out)
    else:
        print("warning: no e-mail can go out (no e-mail plugin, or demo mode)", file=out)
    if print_link:
        print(f"link: {mail.link}", file=out)
    return 0


def main(
    argv: list[str] | None = None,
    session_factory: Callable[[], SessionFactory] | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.modules.auth")
    commands = parser.add_subparsers(dest="command", required=True)
    chairman = commands.add_parser("create-chairman", help="invite the first chairman")
    chairman.add_argument("--email", required=True)
    chairman.add_argument("--force", action="store_true")
    chairman.add_argument("--print-link", action="store_true")
    args = parser.parse_args(argv)

    settings = get_settings()
    plugins = build_plugin_registry(settings)
    validate_auth_settings(settings, plugins)  # the link needs AUTH_HASH_SECRET
    if session_factory is None:
        from src.core.database import async_session_factory

        factory: SessionFactory = async_session_factory
    else:
        factory = session_factory()
    return asyncio.run(
        create_chairman(
            factory,
            settings,
            plugins,
            email=args.email,
            force=args.force,
            print_link=args.print_link,
            out=sys.stdout,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
