"""Exercise actual private subprocess framing with synthetic 25MiB, no LINE call."""

import base64
import shutil

import pytest

from line_agent import additional_connection as additional


@pytest.mark.parametrize("threaded", [False, True])
def test_25_mib_private_frame_reaches_worker(service, tmp_path, monkeypatch, threaded):
    import sys
    if sys.platform == "win32" and not threaded:
        pytest.skip("Windows pipes require threaded reader; covered by threaded case")
    if not shutil.which("node"):
        pytest.skip("node not available")
    reader = additional.PipeChunks
    monkeypatch.setattr(additional, "PipeChunks", lambda stream: reader(stream, threaded=threaded))
    owner = service.line.ids[0]
    service.store.put(
        "additional_connection", "linejs", {"owner": owner, "token": "synthetic", "storage": {}}
    )
    (tmp_path / "worker.mjs").write_text("""
import {createInterface} from 'node:readline';
const lines=createInterface({input:process.stdin,crlfDelay:Infinity});
const {value}=await lines[Symbol.asyncIterator]().next();lines.close();
if(value.length>48*1024*1024) throw Error('too_large');
const req=JSON.parse(value);
const size=Buffer.from(req.payload.data,'base64').length;
process.stdout.write(JSON.stringify({event:'result',result:{size},state:req.state})+'\\n');
""")
    monkeypatch.setattr(additional, "adapter_directory", lambda: tmp_path)
    result = additional.invoke(
        service.store,
        "square_execute",
        owner,
        payload={"data": base64.b64encode(b"x" * (25 * 1024 * 1024)).decode()},
    )
    assert result == {"size": 25 * 1024 * 1024}


def test_thread_reader_drains_output_after_child_exit_and_joins():
    import subprocess
    import sys

    child = subprocess.Popen(
        [sys.executable, "-c", "import sys;sys.stdout.buffer.write(b'x'*(2*1024*1024))"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    reader = additional.PipeChunks(child.stdout, threaded=True)
    size = 0
    try:
        while True:
            data = reader.read(1)
            assert data is not None
            if not data:
                break
            assert data == b"x" * len(data)
            size += len(data)
        assert size == 2 * 1024 * 1024
        assert reader.queue.maxsize == 8
        child.wait(timeout=5)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        reader.close()
        child.stdout.close()
    assert not reader.thread.is_alive()


def test_thread_reader_timeout_and_pipe_failure_are_distinct(monkeypatch):

    def fail(*args):
        raise OSError("synthetic secret must not escape")

    monkeypatch.setattr(additional.os, "read", fail)
    class Descriptor:
        def fileno(self):
            return 99
    reader = additional.PipeChunks(Descriptor(), threaded=True)
    try:
        with pytest.raises(additional.AdditionalFailure) as error:
            reader.read(1)
        assert error.value.stage == "adapter"
        assert error.value.reason == "pipe_read_failed"
        assert "synthetic" not in str(error.value)
        assert reader.read(0.01) is None
    finally:
        reader.close()


def test_frame_uses_bounded_writes_and_reports_failures():
    import json

    class Output:
        def __init__(self):
            self.data = bytearray()
            self.closed = False
        def write(self, part):
            assert len(part) <= 65536
            size = min(len(part), 8192)
            self.data.extend(part[:size])
            return size
        def close(self):
            self.closed = True
    out = Output()
    request = {"value": "x" * 100000}
    additional.write_frame(out, request)
    assert json.loads(out.data) == request and out.closed
    out.write = lambda value: 0
    with pytest.raises(additional.AdditionalFailure) as error:
        additional.write_frame(out, request)
    assert error.value.stage == "adapter_input"
    assert error.value.reason == "pipe_write_failed"


def test_adapter_environment_preserves_windows_bootstrap_without_loaders(monkeypatch):
    monkeypatch.setattr(additional.os, "environ", {
        "SYSTEMROOT": "synthetic-root", "PATH": "synthetic-path",
        "NODE_OPTIONS": "synthetic-loader", "PYTHONPATH": "synthetic-path-injection",
    })
    assert additional.adapter_environment() == {"SYSTEMROOT": "synthetic-root", "PATH": "synthetic-path"}


def test_sanitized_environment_starts_real_node():
    import subprocess
    node = additional.find_executable("node")
    if not node:
        pytest.skip("node not available")
    result = subprocess.run([node, "-e", "process.stdout.write('ready')"], env=additional.adapter_environment(), capture_output=True, timeout=15)
    assert result.returncode == 0, f"Node bootstrap exit code {result.returncode}"
    assert result.stdout == b"ready"


def test_blocked_child_input_is_killed_with_safe_timeout():
    import subprocess
    import sys
    import time

    child = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    began = time.monotonic()
    try:
        with pytest.raises(additional.AdditionalFailure) as error:
            additional.write_frame(child.stdin, {"value": "x" * (2 * 1024 * 1024)}, timeout=0.2, abort=child.kill)
        assert error.value.stage == "adapter_input"
        assert error.value.reason == "pipe_write_timeout"
        assert time.monotonic() - began < 7
        child.wait(timeout=2)
        assert child.returncode is not None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
