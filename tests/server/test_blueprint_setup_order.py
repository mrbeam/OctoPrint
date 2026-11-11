"""Run server blueprint setup with real Flask, without hardware/server imports."""
import ast
import warnings
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from flask import Blueprint, Flask, request
from werkzeug.exceptions import HTTPException


def server_class(namespace):
    source = Path(__file__).parents[2] / "src/octoprint/server/__init__.py"
    tree = ast.parse(source.read_text())
    names = {
        "_setup_blueprints", "_prepare_blueprint_plugins", "_prepare_asset_plugins",
        "_prepare_blueprint_plugin", "_prepare_asset_plugin",
        "_add_plugin_request_handlers_to_blueprints",
    }
    methods = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name in names]
    for method in methods:
        if method.name == "_setup_blueprints":
            # Supply the application-specific imports through the test namespace.
            method.body = [node for node in method.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
    module = ast.Module(body=methods, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(source), "exec"), namespace)
    return type("Server", (), {name: namespace[name] for name in names})


@pytest.mark.parametrize("reject", [False, True])
def test_hooks_installed_before_registration_for_api_logic_and_assets(tmp_path, reject):
    app = Flask(__name__)
    calls = []
    api = Blueprint("api", __name__)
    api.add_url_rule("/probe", view_func=lambda: "api")
    logic = Blueprint("plugin_test_logic", __name__)
    logic.add_url_rule("/probe", view_func=lambda: "logic")
    (tmp_path / "probe.txt").write_text("asset")

    class BlueprintPlugin:
        _identifier = "test"
        get_blueprint = lambda self: logic
        is_blueprint_protected = lambda self: True
        get_blueprint_api_prefixes = lambda self: ["/probe"]

    class AssetPlugin:
        _identifier = "camera"
        get_asset_folder = lambda self: str(tmp_path)

    def before():
        calls.append(("before", request.path))
        if reject:
            return "blocked", 403

    def after(response):
        calls.append(("after", request.path))
        response.headers["X-Hook"] = "installed"
        return response

    manager = Mock()
    manager.get_implementations.side_effect = lambda kind: [kind()]
    manager.get_hooks.side_effect = lambda name: {"guard": lambda **kwargs: [before if name.endswith("before_request") else after]}
    namespace = dict(
        app=app, api=api, Blueprint=Blueprint, OrderedDict=OrderedDict,
        request=request, HTTPException=HTTPException,
        make_api_error=lambda message, code: (message, code),
        corsRequestHandler=lambda: None, loginFromApiKeyRequestHandler=lambda: None,
        requireLoginRequestHandler=lambda: None, corsResponseHandler=lambda response: response,
        octoprint=SimpleNamespace(plugin=SimpleNamespace(
            plugin_manager=lambda: manager, BlueprintPlugin=BlueprintPlugin, AssetPlugin=AssetPlugin)),
    )
    server = server_class(namespace)()
    server._logger = Mock()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        server._setup_blueprints()
    assert set(app.blueprints) == {"api", "plugin_test_logic", "plugin_camera_assets"}
    client = app.test_client()
    for path in ("/api/probe", "/plugin/test/probe", "/plugin/camera/static/probe.txt"):
        response = client.get(path)
        assert response.status_code == (403 if reject else 200)
        assert response.headers["X-Hook"] == "installed"
        assert calls.count(("before", path)) == 1
        assert calls.count(("after", path)) == 1
