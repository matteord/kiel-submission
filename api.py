"""Narrow HTTPS-only coordinator client; compatible with MisConn v2 leasing endpoints."""
from __future__ import annotations
import requests
from urllib.parse import urlsplit


class Coordinator:
    def __init__(self, api_url: str, token: str | None = None, session=None):
        self.base = api_url.rstrip('/')
        parsed = urlsplit(self.base)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
            raise ValueError('Production coordinator URL must be an HTTPS origin without credentials')
        self.token = token
        self.session = session or requests.Session()
        self.session.trust_env = False  # Coordinator and pairing traffic always connect directly.
        self.session.headers.update({'User-Agent': 'MisConn-Collector/0.3'})

    def post(self, path, payload=None, authenticated=True):
        headers = {}
        if authenticated:
            if not self.token:
                raise RuntimeError('Not paired')
            headers['Authorization'] = 'Bearer ' + self.token
        response = self.session.post(self.base + path, json=payload or {}, headers=headers,
                                     timeout=(10, 40), allow_redirects=False, verify=True)
        if response.status_code == 409 or (response.status_code == 404 and path.startswith('/v1/batches/')):
            raise LeaseExpired('Lease no longer active')
        if response.status_code in (401, 403):
            raise AuthenticationError('Coordinator rejected this device or pairing code')
        if not response.ok:
            raise RuntimeError(f'Coordinator error HTTP {response.status_code}')
        return response.json()

    def revoke(self):
        return self.post('/v1/devices/self/revoke')

    def pair(self, code: str, device_name: str):
        return self.post('/v1/pair', {'invitation': code.strip(), 'device_name': device_name}, False)

    def lease(self):
        return self.post('/v1/lease')

    def heartbeat(self, lease_id):
        return self.post(f'/v1/batches/{lease_id}/heartbeat')

    def complete(self, lease_id, results):
        return self.post(f'/v1/batches/{lease_id}/complete', {'results': results})


class LeaseExpired(Exception):
    pass

class AuthenticationError(Exception):
    pass
