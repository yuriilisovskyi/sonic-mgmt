"""Helpers to keep the local SSH known_hosts file in sync with the testbed devices.

Connections to network devices (``network_cli``) verify the SSH host key of the device against the
local known_hosts file no matter what ``host_key_checking`` is set to in ``ansible.cfg``: starting
from ansible.netcommon 8.5.0 ``network_cli`` carries its own copy of the paramiko connection, and
that copy resolves ``host_key_checking``/``host_key_auto_add`` from hardcoded defaults instead of
the ansible configuration. Testbed neighbors are re-deployed with a new SSH host key from time to
time, so the key recorded locally goes stale and every module run against such a device fails with
"host key mismatch for <address>".
"""

import fcntl
import logging
import os
import re
import subprocess
import threading

from contextlib import contextmanager

logger = logging.getLogger(__name__)

DEFAULT_KNOWN_HOSTS_FILE = os.path.join(os.path.expanduser('~'), '.ssh', 'known_hosts')

KEYSCAN_TIMEOUT = 15

# Reported by the ssh, paramiko and libssh based connection plugins when the key recorded in the
# known_hosts file differs from the key offered by the device, or when no key is recorded at all.
SSH_HOST_KEY_ERRORS = (
    'host key mismatch',
    'host key verification failed',
    'remote host identification has changed',
    'authenticity of host',
)

_HOST_IN_ERROR_PATTERNS = (
    re.compile(r"host key mismatch for ([^\s,'\"]+)", re.IGNORECASE),
    re.compile(r"authenticity of host '([^']+)'", re.IGNORECASE),
)

# The known_hosts file is shared by everything running under this user, updates from the test
# threads of a single test run are serialized with a lock and updates from concurrent test runs
# are serialized with a lock file.
_update_lock = threading.Lock()


def is_ssh_host_key_error(msg):
    """Check whether an ansible module failure message is about the SSH host key of a device."""
    if not msg:
        return False
    msg = str(msg).lower()
    return any(error in msg for error in SSH_HOST_KEY_ERRORS)


def get_ssh_host_key_error_host(msg):
    """Extract the address of the device from an SSH host key error message, None if not reported."""
    if not msg:
        return None
    for pattern in _HOST_IN_ERROR_PATTERNS:
        match = pattern.search(str(msg))
        if match:
            return match.group(1)
    return None


def refresh_ssh_host_key(host, port=None, known_hosts_file=DEFAULT_KNOWN_HOSTS_FILE):
    """Record the current SSH host key of a device, replacing the key recorded for it before.

    Args:
        host: address of the device, optionally in the "[address]:port" form.
        port: SSH port of the device, 22 is assumed when not specified.
        known_hosts_file: the known_hosts file to update.

    Returns:
        True when the current host key of the device was recorded, False otherwise.
    """
    host, port = _split_host_port(host, port)

    host_keys = _scan_host_keys(host, port)
    if not host_keys:
        return False

    with _update_lock, _lock_known_hosts(known_hosts_file):
        _remove_host_keys(host, port, known_hosts_file)
        with open(known_hosts_file, 'a') as known_hosts:
            known_hosts.write('\n'.join(host_keys) + '\n')

    logger.info('Recorded the current SSH host key of {} in {}'.format(host, known_hosts_file))
    return True


def _split_host_port(host, port=None):
    """Split an "[address]:port" entry, the address is returned as is when no port is included."""
    match = re.match(r'^\[(?P<host>.+)\]:(?P<port>\d+)$', host.strip())
    if match:
        return match.group('host'), int(match.group('port'))
    return host.strip(), port


def _scan_host_keys(host, port=None):
    cmd = ['ssh-keyscan', '-T', str(KEYSCAN_TIMEOUT)]
    if port:
        cmd += ['-p', str(port)]
    cmd.append(host)

    stdout, stderr = _run_ssh_util(cmd, timeout=KEYSCAN_TIMEOUT * 2)
    host_keys = [line for line in stdout.splitlines() if line.strip() and not line.startswith('#')]
    if not host_keys:
        logger.warning('Failed to scan the SSH host key of {}: {}'.format(host, stderr.strip()))

    return host_keys


def _remove_host_keys(host, port, known_hosts_file):
    if not os.path.exists(known_hosts_file):
        return

    entry = '[{}]:{}'.format(host, port) if port else host
    _run_ssh_util(['ssh-keygen', '-f', known_hosts_file, '-R', entry], timeout=KEYSCAN_TIMEOUT)


def _run_ssh_util(cmd, timeout):
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=timeout)
        return proc.stdout, proc.stderr
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning('Failed to run "{}": {}'.format(' '.join(cmd), repr(e)))
        return '', repr(e)


@contextmanager
def _lock_known_hosts(known_hosts_file):
    ssh_dir = os.path.dirname(known_hosts_file)
    if ssh_dir:
        os.makedirs(ssh_dir, mode=0o700, exist_ok=True)

    with open(known_hosts_file + '.sonic-mgmt.lock', 'a') as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
