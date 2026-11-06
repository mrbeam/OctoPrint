"""Plugin transport hooks preserve native authentication and origin policy."""
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from tornado.websocket import WebSocketHandler

from octoprint.server.util.sockjs import PrinterStateConnection
from octoprint.vendor.sockjs.tornado.websocket import SockJSWebSocketHandler


def connection(hook):
    client = object.__new__(PrinterStateConnection)
    client._pluginManager = Mock()
    client._pluginManager.get_hooks.return_value = {"test": hook}
    client._userManager = Mock()
    client._logger = Mock()
    client._on_login = Mock()
    client._register = Mock()
    client.close = Mock()
    return client


def test_consumed_message_does_not_reach_native_authentication():
    hook = Mock(return_value=True)
    client = connection(hook)
    client.on_message(json.dumps({"auth": "user:session"}))
    hook.assert_called_once_with(client, {"auth": "user:session"})
    client._userManager.validate_user_session.assert_not_called()


def test_unconsumed_message_retains_native_authentication():
    client = connection(Mock(return_value=False))
    client.on_message(json.dumps({"auth": "user:session"}))
    client._userManager.validate_user_session.assert_called_once_with("user", "session")
    client._on_login.assert_called_once_with(client._userManager.find_user.return_value)


def test_hook_failure_closes_connection_without_native_authentication():
    client = connection(Mock(side_effect=RuntimeError("failure")))
    client.on_message(json.dumps({"auth": "user:session"}))
    client.close.assert_called_once()
    client._userManager.validate_user_session.assert_not_called()


@pytest.mark.parametrize("payload", [None, [], 1, "text"])
def test_non_object_frames_do_not_reach_hooks(payload):
    hook = Mock()
    client = connection(hook)
    client.on_message(json.dumps(payload))
    hook.assert_not_called()


@pytest.mark.parametrize(
    "same_origin, grant, expected",
    [
        (True, False, True),
        (False, True, True),
        (False, False, False),
        (False, None, False),
        (False, "yes", False),
    ],
)
def test_origin_hook_requires_explicit_grant(same_origin, grant, expected):
    handler = object.__new__(SockJSWebSocketHandler)
    handler.request = Mock()
    handler.server = SimpleNamespace(settings={"websocket_allow_origin": ""})
    hook = Mock(return_value=grant)
    manager = Mock()
    manager.get_hooks.return_value = {"test": hook}
    with patch.object(
        WebSocketHandler, "check_origin", return_value=same_origin
    ), patch("octoprint.plugin.plugin_manager", return_value=manager):
        assert handler.check_origin("https://dev.mr-beam.io") is expected
    if same_origin:
        hook.assert_not_called()
    else:
        hook.assert_called_once_with("https://dev.mr-beam.io", handler.request)
