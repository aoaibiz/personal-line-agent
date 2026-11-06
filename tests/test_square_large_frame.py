"""Exercise actual private subprocess framing with synthetic 25MiB, no LINE call."""

import base64
import shutil

import pytest

from line_agent import additional_connection as additional


def test_25_mib_private_frame_reaches_worker(service, tmp_path, monkeypatch):
    if not shutil.which("node"):
        pytest.skip("node not available")
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
