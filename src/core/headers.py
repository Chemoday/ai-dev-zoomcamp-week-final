"""Response headers every answer carries (docs/ARCHITECTURE.md 4.8)."""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

# A sign-in link page never leaks its URL to another site (the token is in the fragment, which
# browsers do not send anyway; this closes the rest).
_HEADERS = ((b"referrer-policy", b"no-referrer"),)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                names = {name.lower() for name, _ in message.get("headers", [])}
                extra = [(name, value) for name, value in _HEADERS if name not in names]
                message["headers"] = [*message.get("headers", []), *extra]
            await send(message)

        await self.app(scope, receive, send_with_headers)
