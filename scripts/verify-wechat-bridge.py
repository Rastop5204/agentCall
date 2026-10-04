#!/usr/bin/env python3
"""Smoke-test the private WeChat bridge without network access, login, or real credentials."""
import argparse
import json
import re
import subprocess
import time
import uuid


LABEL = 'agentcall.verification-owner'
INITIALIZE = r'''
const fs = require('fs');
fs.writeFileSync('/run/agentcall/agentcall-wechat-token',
  'puppet_' + require('crypto').randomBytes(24).toString('hex') + '\n', {mode: 0o600});
require('/app/service.cjs');
'''
PROBE = r'''
const fs = require('fs');
const {grpc, PuppetClient, VersionRequest} = require('/app/node_modules/wechaty-grpc');
const client = new PuppetClient('127.0.0.1:8788', grpc.credentials.createInsecure());
const timeout = setTimeout(() => process.exit(1), 5000);
function version(metadata) {
  return new Promise((resolve, reject) => client.version(new VersionRequest(), metadata,
    (error, result) => error ? reject(error) : resolve(result)));
}
(async () => {
  try {
    await version(new grpc.Metadata());
    throw new Error('Unauthenticated call unexpectedly succeeded');
  } catch (error) {
    if (error.code !== grpc.status.UNAUTHENTICATED) throw error;
  }
  const metadata = new grpc.Metadata();
  metadata.set('authorization', 'Wechaty ' + fs.readFileSync('/run/agentcall/agentcall-wechat-token', 'utf8').trim());
  await version(metadata);
  client.close();
  clearTimeout(timeout);
  console.log('Authenticated gRPC works; unauthorized calls are rejected; runtime ' + process.version);
})().catch(() => process.exit(1));
'''


def docker(*arguments, required=True, timeout=45):
    result = subprocess.run(['docker', *arguments], capture_output=True, text=True, timeout=timeout)
    if result.returncode and required:
        raise RuntimeError('Docker ' + arguments[0] + ' failed')
    output = result.stdout + (result.stderr if arguments[0] == 'logs' else '')
    return output.strip() if result.returncode == 0 else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='agentcall-agentcall-wechat:latest')
    args = parser.parse_args()
    owner = uuid.uuid4().hex
    name = 'agentcall-wechat-verify-' + owner[:12]
    try:
        docker('run', '--detach', '--name', name, '--label', LABEL + '=' + owner,
               '--network', 'none', '--read-only', '--cap-drop', 'ALL',
               '--security-opt', 'no-new-privileges:true', '--init',
               '--tmpfs', '/tmp:size=256m,mode=1777',
               '--tmpfs', '/data:uid=10001,gid=10001,mode=700',
               '--tmpfs', '/run/agentcall:uid=10001,gid=10001,mode=700',
               '-e', 'AGENTCALL_WECHAT_LOCAL_ONLY=1', '--entrypoint', 'node', args.image, '-e', INITIALIZE)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            result = docker('exec', name, 'node', '-e', PROBE, required=False, timeout=30)
            if result:
                print('PASS: ' + result)
                return 0
            info = json.loads(docker('inspect', name))[0]
            if not info['State']['Running']:
                raise RuntimeError('Bridge container exited before becoming ready')
            time.sleep(1)
        raise RuntimeError('Bridge authentication probe did not pass before the deadline')
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        print('FAIL: ' + str(error))
        logs = docker('logs', '--tail', '12', name, required=False)
        if logs:
            print(re.sub(r'puppet_[a-f0-9]+', '[redacted]', logs))
        return 1
    finally:
        raw = docker('inspect', name, required=False)
        if raw and json.loads(raw)[0]['Config']['Labels'].get(LABEL) == owner:
            docker('rm', '--force', name)


if __name__ == '__main__':
    raise SystemExit(main())
