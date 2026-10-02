"""Host-side component checks for linuxai's update CLI; never restarts services."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FILES = ('Dockerfile', 'requirements.txt', 'channel5.py', 'renew.py',
         'server.py', 'run.py', 'compose.yaml')


def command(args):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f'{args[0]} component probe failed: {result.stderr[-1000:]}')
    return result.stdout


def source_digest():
    digest = hashlib.sha256()
    for name in FILES:
        digest.update(name.encode() + b'\0' + (ROOT / name).read_bytes() + b'\0')
    return digest.hexdigest()


def metadata():
    base = next(line.split()[1] for line in (ROOT / 'Dockerfile').read_text().splitlines()
                if line.startswith('FROM '))
    command(['docker', 'pull', base])
    image = json.loads(command(['docker', 'image', 'inspect', base]))[0]
    env = dict(item.split('=', 1) for item in image['Config']['Env'] if '=' in item)
    return {'base_image': base, 'base_id': image['Id'],
            'python_version': env.get('PYTHON_VERSION', 'unknown'),
            'source_digest': source_digest()}


PROBE = r'''
import importlib.metadata,json,re,subprocess,sys
def run(args):
    p=subprocess.run(args,capture_output=True,text=True)
    if p.returncode: raise RuntimeError(p.stderr[-1000:])
    return p.stdout
run(['apt-get','update','-qq'])
plan=run(['apt-get','--simulate','--with-new-pkgs','upgrade'])
rows=[]
for line in plan.splitlines():
    m=re.match(r'Inst (\S+)(?: \[([^]]+)\])? \((\S+)',line)
    if m: rows.append({'component':m[1],'installed':m[2] or 'not installed','available':m[3],'update':True})
packages={}
for name in ['chromium','ca-certificates']:
    installed=run(['dpkg-query','-W','-f=${Version}',name]).strip()
    packages[name]=installed
    if not any(row['component']==name for row in rows):
        rows.append({'component':name,'installed':installed,'available':installed,'update':False})
run([sys.executable,'-m','pip','install','--disable-pip-version-check','--dry-run',
     '--upgrade','--report','/tmp/pip-report.json','pip','-r','/tmp/channel5-requirements.txt'])
pip_plan=json.load(open('/tmp/pip-report.json'))
for item in pip_plan['install']:
    name=item['metadata']['name']; available=item['metadata']['version']
    try: installed=importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError: installed='not installed'
    rows.append({'component':name,'installed':installed,'available':available,'update':installed!=available})
for name in ['websocket-client','pip']:
    if not any(row['component'].lower()==name for row in rows):
        installed=importlib.metadata.version(name)
        rows.append({'component':name,'installed':installed,'available':installed,'update':False})
print(json.dumps({'rows':rows,'python_version':sys.version.split()[0]}))
'''


def check(container):
    latest = metadata()
    running = json.loads(command(['docker', 'inspect', container]))[0]
    if not running['State']['Running']:
        raise RuntimeError('Channel 5 container is not running')
    image = json.loads(command(['docker', 'image', 'inspect', running['Image']]))[0]
    labels = image['Config'].get('Labels') or {}
    probe = json.loads(command([
        'docker', 'run', '--rm', '--user', '0', '--entrypoint', 'python',
        '--mount', f'type=bind,src={ROOT / "requirements.txt"},dst=/tmp/channel5-requirements.txt,readonly',
        running['Image'], '-c', PROBE]))
    rows = probe['rows']
    installed_base = labels.get('io.channel5.base-image-id')
    rows.insert(0, {'component': 'Python runtime', 'installed': probe['python_version'],
                   'available': latest['python_version'],
                   'update': probe['python_version'] != latest['python_version']})
    rows.append({'component': 'Python base image',
                 'installed': (installed_base or 'untracked').replace('sha256:', '')[:12],
                 'available': latest['base_id'].replace('sha256:', '')[:12],
                 'update': installed_base != latest['base_id']})
    digest = labels.get('io.channel5.source-digest')
    rows.append({'component': 'local source/recipe', 'installed': (digest or 'untracked')[:12],
                 'available': latest['source_digest'][:12],
                 'update': digest != latest['source_digest']})
    return {**latest, 'rows': rows, 'updates': sum(row['update'] for row in rows)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--metadata', action='store_true')
    parser.add_argument('--container', default='channel5-stream')
    args = parser.parse_args()
    try:
        result = metadata() if args.metadata else check(args.container)
        print(json.dumps(result))
    except Exception as exc:
        raise SystemExit(str(exc))
