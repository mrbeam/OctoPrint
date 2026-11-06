"""Terminal batches preserve the same backlog semantics as individual lines."""
import threading
from unittest.mock import Mock

from octoprint.server.util.sockjs import PrinterStateConnection


def connection():
    result = object.__new__(PrinterStateConnection)
    result._logBacklog = []
    result._logBacklogMutex = threading.Lock()
    return result


def test_batches_preserve_order_between_individual_lines():
    client = connection()
    client.on_printer_add_log("before")
    client.on_printer_add_logs(["first", "second"])
    client.on_printer_add_logs([])
    client.on_printer_add_log("after")
    assert client._logBacklog == ["before", "first", "second", "after"]


def test_batch_is_consumed_under_backlog_lock():
    client = connection()

    def lines():
        assert client._logBacklogMutex.locked()
        yield "first"
        assert client._logBacklogMutex.locked()
        yield "second"

    client.on_printer_add_logs(lines())
    assert client._logBacklog == ["first", "second"]
    assert not client._logBacklogMutex.locked()


def test_batch_acquires_lock_once():
    client = connection()
    lock = Mock(wraps=client._logBacklogMutex)

    class CountingLock:
        def __enter__(self):
            lock.acquire()

        def __exit__(self, *args):
            lock.release()

    client._logBacklogMutex = CountingLock()
    client.on_printer_add_logs(["first", "second", "third"])
    lock.acquire.assert_called_once_with()
    lock.release.assert_called_once_with()
