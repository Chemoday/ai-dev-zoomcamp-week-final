"""Push to the native phone app through Firebase Cloud Messaging (plugin "common.fcm_push").

Stub only (epic E1, task N7, issue #57): the design is docs/epics/E1_NOTIFICATIONS.md 6.4 and the
device endpoint docs/API.md 6 ("Mobile push"). Until N7 is implemented, enabling the plugin stops
the app at startup, so it can never be switched on by accident.

What N7 adds here:
- read FCM_PROJECT_ID and the service account file; get and cache an OAuth 2 access token
  (scope FCM_SCOPE) with google-auth;
- build_message(): the FCM v1 message (notification, data, Android and APNs priority, time to live,
  collapse key) from an OutgoingMessage;
- send(): one POST to FCM_SEND_URL per device (10 s timeout, no redirects) and map the FCM error
  status to a DeliveryResult (table in the design page); never log the token or the texts.
"""

from typing import Any

from src.plugins.registry import DeliveryResult, OutgoingMessage, PluginRegistry

CHANNEL_NAME = "mobile_push"
FCM_SEND_URL = "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"


class FcmPushChannel:
    """NotificationChannel for the "mobile_push" channel (not implemented yet)."""

    name = CHANNEL_NAME

    def __init__(self, project_id: str, service_account_file: str) -> None:
        self._project_id = project_id
        self._service_account_file = service_account_file

    def build_message(self, message: OutgoingMessage) -> dict[str, Any]:
        """The JSON body of an FCM v1 send request for message.address["token"]."""
        raise NotImplementedError("common.fcm_push is planned (E1/N7)")

    async def send(self, message: OutgoingMessage) -> DeliveryResult:
        raise NotImplementedError("common.fcm_push is planned (E1/N7)")


def register(registry: PluginRegistry) -> None:
    raise RuntimeError(
        "plugin common.fcm_push is not implemented yet (E1/N7, issue #57); "
        "remove it from ENABLED_PLUGINS"
    )
