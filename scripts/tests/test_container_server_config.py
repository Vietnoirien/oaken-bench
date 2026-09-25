import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, 'docker'))
from configure_server import configure  # noqa: E402


def test_server_url_override_reaches_both_harness_configs(tmp_path):
    (tmp_path / '.pi' / 'agent').mkdir(parents=True)
    (tmp_path / '.dsh').mkdir()
    (tmp_path / '.pi' / 'agent' / 'models.json').write_text(json.dumps({
        'providers': {'local-llama': {'baseUrl': 'http://llama:8080/v1'}}
    }))
    dsh_path = tmp_path / '.dsh' / 'settings.yaml'
    dsh_path.write_text('local-llama:\n  baseURL: http://llama:8080/v1\n')

    configure(str(tmp_path), 'http://172.17.0.1:8081/v1')

    pi = json.loads((tmp_path / '.pi' / 'agent' / 'models.json').read_text())
    assert pi['providers']['local-llama']['baseUrl'] == 'http://172.17.0.1:8081/v1'
    assert 'baseURL: http://172.17.0.1:8081/v1' in dsh_path.read_text()


def test_missing_dsh_endpoint_fails_closed(tmp_path):
    import pytest

    (tmp_path / '.pi' / 'agent').mkdir(parents=True)
    (tmp_path / '.dsh').mkdir()
    (tmp_path / '.pi' / 'agent' / 'models.json').write_text(json.dumps({
        'providers': {'local-llama': {'baseUrl': 'http://llama:8080/v1'}}
    }))
    (tmp_path / '.dsh' / 'settings.yaml').write_text('local-llama:\n  baseURL: http://other:8080/v1\n')
    with pytest.raises(ValueError, match='refusing to run'):
        configure(str(tmp_path), 'http://llama:8082/v1')


def test_server_url_requires_openai_compatible_v1_suffix(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        configure(str(tmp_path), 'http://example.test:8081')
