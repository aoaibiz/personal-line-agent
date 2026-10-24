from cryptography.fernet import Fernet
from test_service import FakeLine

from line_agent.lifecycle import diagnose
from line_agent.service import AgentService
from line_agent.store import Store


def test_saved_identity_is_not_a_live_connection(tmp_path):
    service = AgentService(Store(tmp_path / 'a', Fernet.generate_key()), FakeLine())
    service.store.put('setting', 'identity', {'mid': 'owner'})
    result = diagnose(service)
    assert result['identity_saved'] is True
    assert result['connection_verified'] is None
    service.line.profile = lambda: {'mid': 'different', 'displayName': 'not emitted'}
    result = diagnose(service, probe=True)
    assert result['connection_verified'] is False
    assert result['reason'] == 'identity_mismatch'
    assert 'not emitted' not in str(result)


def test_probe_preserves_pause_and_unknown(tmp_path):
    service = AgentService(Store(tmp_path / 'a', Fernet.generate_key()), FakeLine())
    service.store.put('setting', 'identity', {'mid': 'owner'})
    service.store.put('action', 'a', {'id': 'a', 'state': 'unknown'})
    service.pause()
    service.line.profile = lambda: {'mid': 'owner', 'displayName': 'private'}
    result = diagnose(service, probe=True)
    assert result['connection_verified'] is True
    assert result['paused'] is True and result['uncertain_operations'] == 1
    assert service.store.get('action', 'a')['state'] == 'unknown'
    assert 'private' not in str(result)


def test_probe_failure_sanitizes_upstream(tmp_path):
    service = AgentService(Store(tmp_path / 'a', Fernet.generate_key()), FakeLine())
    service.store.put('setting', 'identity', {'mid': 'owner'})
    def fail():
        raise RuntimeError('SECRET WOULD BE HERE')
    service.line.profile = fail
    result = diagnose(service, probe=True)
    assert result['connection_verified'] is False
    assert 'SECRET' not in str(result)
