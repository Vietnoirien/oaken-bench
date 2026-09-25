#!/usr/bin/env python3
"""Apply a run's OpenAI-compatible base URL to both harness configs."""
import json
import os
import sys


def configure(config_dir, base_url):
    if not base_url.startswith(('http://', 'https://')) or not base_url.rstrip('/').endswith('/v1'):
        raise ValueError('server URL must be an http(s) URL ending in /v1')
    pi_path = os.path.join(config_dir, 'pi', 'models.json')
    with open(pi_path, encoding='utf-8') as f:
        pi = json.load(f)
    pi['providers']['local-llama']['baseUrl'] = base_url
    with open(pi_path, 'w', encoding='utf-8') as f:
        json.dump(pi, f, indent=2)
        f.write('\n')
    dsh_path = os.path.join(config_dir, 'dsh', 'settings.yaml')
    with open(dsh_path, encoding='utf-8') as f:
        dsh = f.read()
    dsh = dsh.replace('baseURL: http://llama:8080/v1', f'baseURL: {base_url}')
    with open(dsh_path, 'w', encoding='utf-8') as f:
        f.write(dsh)


if __name__ == '__main__':
    configure(sys.argv[1], os.environ['OAKEN_SERVER_URL'])
