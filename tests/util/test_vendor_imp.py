"""Compatibility checks for the vendored legacy plugin loader."""
import pytest

from octoprint.vendor import imp


def test_missing_module_has_standard_import_error():
    with pytest.raises(ImportError) as caught:
        imp.find_module("missing_test_plugin", [])
    assert caught.value.name == "missing_test_plugin"
    assert str(caught.value) == "No module named 'missing_test_plugin'"


def test_find_and_load_source_plugin(tmp_path):
    (tmp_path / "test_plugin.py").write_text("VALUE = 42\n")
    stream, filename, description = imp.find_module("test_plugin", [str(tmp_path)])
    try:
        module = imp.load_module("test_plugin", stream, filename, description)
        assert module.VALUE == 42
    finally:
        if stream is not None:
            stream.close()
        import sys
        sys.modules.pop("test_plugin", None)
