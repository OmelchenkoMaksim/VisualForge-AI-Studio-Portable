"""Staging-only outbound guard; preserve local IPC and record denied attempts."""
import ipaddress
import json
import os
import socket
import subprocess
import time
import importlib.abc
import importlib.machinery
import sys

def permitted(host):
    if host in ('localhost', None, ''):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False

def deny(target):
    path = os.environ.get('VISUALFORGE_OFFLINE_LOG')
    if path:
        with open(path, 'a', encoding='utf-8') as stream:
            stream.write(json.dumps({'time': time.time(), 'denied_target': str(target)}) + '\n')
    raise OSError('VisualForge staging offline guard denied outbound network: ' + str(target))

_resolve = socket.getaddrinfo
def resolve(host, *args, **kwargs):
    if not permitted(host):
        deny(host)
    return _resolve(host, *args, **kwargs)
socket.getaddrinfo = resolve
_connect = socket.socket.connect
def connect(self, address):
    if isinstance(address, tuple) and not permitted(address[0]):
        deny(address[0])
    return _connect(self, address)
socket.socket.connect = connect
_popen = subprocess.Popen
class OfflinePopen(_popen):
    def __init__(self, args, *positional, **kwargs):
        from urllib.parse import urlsplit
        arguments = [args] if isinstance(args, str) else args
        for arg in arguments:
            if isinstance(arg, str) and arg.startswith(('http://', 'https://')) and not permitted(urlsplit(arg).hostname):
                deny(arg)
        super().__init__(args, *positional, **kwargs)
subprocess.Popen = OfflinePopen

class NetworkLoader(importlib.abc.Loader):
    def __init__(self, original):
        self.original = original
    def create_module(self, spec):
        return self.original.create_module(spec) if hasattr(self.original, 'create_module') else None
    def exec_module(self, module):
        self.original.exec_module(module)
        def blocked(*args, **kwargs):
            deny('FreeVideo managed download (offline staging)')
        for name in ('download', 'download_here', 'clone'):
            if hasattr(module, name):
                setattr(module, name, blocked)

class NetworkFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname != 'freevideo_engine.network':
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec and spec.loader:
            spec.loader = NetworkLoader(spec.loader)
        return spec
sys.meta_path.insert(0, NetworkFinder())
