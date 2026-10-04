#!/usr/bin/env python3
"""Retire the identified legacy service without deleting its container or volume."""
import argparse
import json
import subprocess


def docker(*arguments, required=True):
    result = subprocess.run(['docker', *arguments], text=True, capture_output=True, check=False)
    if result.returncode and required:
        raise RuntimeError('Docker 操作失败：' + arguments[0])
    return result.stdout if result.returncode == 0 else None


def legacy_container():
    raw = docker('inspect', 'emailcall', required=False)
    if raw is None:
        return None
    item = json.loads(raw)[0]
    labels = item.get('Config', {}).get('Labels') or {}
    owned = (labels.get('com.docker.compose.project') == 'emailcall'
             and labels.get('com.docker.compose.service') == 'emailcall'
             and any(mount.get('Name') == 'emailcall-data' and mount.get('Destination') == '/data'
                     for mount in item.get('Mounts', [])))
    if not owned:
        raise RuntimeError('名为 emailcall 的容器无法确认属于本应用，已停止迁移，请检查 Docker。')
    return item


def migrate():
    item = legacy_container()
    if not item:
        return False
    running = bool(item.get('State', {}).get('Running'))
    docker('update', '--restart=no', 'emailcall')
    if running:
        docker('stop', '--time', '25', 'emailcall')
    return running


def restore():
    if legacy_container():
        docker('update', '--restart=unless-stopped', 'emailcall')
        docker('start', 'emailcall')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    try:
        if args.restore:
            restore()
        else:
            print('running' if migrate() else 'stopped')
    except (RuntimeError, OSError, ValueError) as error:
        raise SystemExit(str(error)) from None


if __name__ == '__main__':
    main()
