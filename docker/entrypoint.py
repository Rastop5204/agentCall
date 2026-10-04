"""Initialize private transport credentials, then replace this process with the gateway."""
import os
from pathlib import Path
import secrets
import sys


def ensure_wechat_token(directory):
    directory = Path(directory) / 'wechat-bridge'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    path = directory / 'agentcall-wechat-token'
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if not path.is_file() or not path.read_text().strip():
            raise RuntimeError('微信凭据文件无效，请检查数据卷。') from None
        path.chmod(0o600)
    else:
        with os.fdopen(descriptor, 'w') as output:
            output.write('puppet_' + secrets.token_hex(24) + '\n')
            output.flush()
            os.fsync(output.fileno())
    return path


if __name__ == '__main__':
    ensure_wechat_token(os.environ.get('AGENTCALL_DATA_DIR', os.environ.get('EMAILCALL_DATA_DIR', '/data')))
    os.execv(sys.executable, [sys.executable, '-m', 'gateway'])
