#!/usr/bin/env python3
"""Generate a server-only Dockerfile with persistent dependency download caches."""
import json
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile

DEFAULT_PIP_INDEX_URL = 'https://pypi.tuna.tsinghua.edu.cn/simple'
IMAGE_NAMES = {'PYTHON_BASE_IMAGE': 'python', 'NODE_BASE_IMAGE': 'node', 'NGINX_BASE_IMAGE': 'nginx'}


def image_identity(reference):
    name, separator, digest = reference.partition('@')
    repository, colon, tag = name.rpartition(':')
    if not colon or not repository or not tag:
        raise ValueError('Base image must include an explicit tag: ' + reference)
    image = repository.rsplit('/', 1)[-1]
    official_repositories = {
        image, 'library/' + image, 'docker.io/library/' + image,
        'docker.m.daocloud.io/library/' + image,
    }
    if image not in IMAGE_NAMES.values() or repository not in official_repositories:
        raise ValueError('Unsupported base image repository: ' + repository)
    if separator and not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        raise ValueError('Invalid base image digest: ' + reference)
    return image, tag, digest


def load_base_images(path):
    if not path.is_file():
        raise ValueError('Missing base image lock file: ' + str(path))
    images = {}
    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        words = shlex.split(line, comments=True)
        if not words:
            continue
        if len(words) != 1 or '=' not in words[0]:
            raise ValueError(f'Invalid base image configuration at line {number}')
        key, value = words[0].split('=', 1)
        if key not in IMAGE_NAMES or key in images:
            raise ValueError('Unknown or duplicate base image key: ' + key)
        image, tag, digest = image_identity(value)
        if image != IMAGE_NAMES[key] or not digest:
            raise ValueError('Base image must match its key and include a SHA256 digest: ' + key)
        images[key] = value
    if images.keys() != IMAGE_NAMES.keys():
        raise ValueError('Base image lock must contain Python, Node and Nginx')
    return images


def pin_base_images(source, images, required):
    lines = source.splitlines(keepends=True)
    arguments = {}
    build_args = {}
    stages = set()
    found = set()
    global_scope = True
    for index, line in enumerate(lines):
        argument = re.fullmatch(r'ARG\s+([A-Za-z_][A-Za-z0-9_]*)=(\S+)\s*', line.strip())
        if global_scope and argument:
            key, default = argument.groups()
            if key in arguments:
                raise ValueError('Duplicate global build argument: ' + key)
            arguments[key] = (index, default)
        if not re.match(r'^\s*FROM\s', line, re.IGNORECASE):
            continue
        global_scope = False
        instruction = re.fullmatch(r'\s*FROM\s+(\S+)(?:\s+AS\s+([A-Za-z0-9_.-]+))?\s*', line, re.IGNORECASE)
        if not instruction:
            raise ValueError('Unsupported FROM instruction; review branch Dockerfile: ' + line.strip())
        reference, alias = instruction.groups()
        if reference in stages:
            if alias:
                stages.add(alias)
            continue
        variable = re.fullmatch(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)', reference)
        key = None
        if variable:
            key = variable.group(1) or variable.group(2)
            if key not in images or key not in arguments:
                raise ValueError('Unsupported base image build argument: ' + key)
            reference = arguments[key][1]
        image, tag, digest = image_identity(reference)
        role = next(role for role, name in IMAGE_NAMES.items() if name == image)
        if role not in required or (key is not None and key != role):
            raise ValueError('Unexpected base image in branch Dockerfile: ' + reference)
        pinned = images[role]
        _, pinned_tag, pinned_digest = image_identity(pinned)
        if tag != pinned_tag or (digest and digest != pinned_digest):
            raise ValueError(f'Branch base image {reference} conflicts with locked {pinned}; review the lock before deployment')
        if variable:
            argument_index = arguments[key][0]
            lines[argument_index] = f'ARG {key}={pinned}\n'
            build_args[key] = pinned
        else:
            suffix = ' AS ' + alias if alias else ''
            lines[index] = 'FROM ' + pinned + suffix + '\n'
        found.add(role)
        if alias:
            stages.add(alias)
    if found != required:
        raise ValueError('Branch Dockerfile is missing expected base images: ' + ', '.join(sorted(required - found)))
    return ''.join(lines), build_args


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError('Upstream Dockerfile changed; review cache customization: ' + old[:70])
    return source.replace(old, new, 1)


def prepare(source, pip_index_url=DEFAULT_PIP_INDEX_URL):
    source = replace_once(source, ' PIP_NO_CACHE_DIR=1', '')
    source = replace_once(source, 'RUN sed -i', '''RUN --mount=type=cache,id=enrui-apt-packages,target=/var/cache/apt,sharing=locked \\
    --mount=type=cache,id=enrui-apt-lists,target=/var/lib/apt/lists,sharing=locked \\
    rm -f /etc/apt/apt.conf.d/docker-clean \\
    && printf 'APT::Keep-Downloaded-Packages "true";\\nBinary::apt::APT::Keep-Downloaded-Packages "true";\\n' > /etc/apt/apt.conf.d/keep-cache \\
    && (sed -i''')
    source = replace_once(source, '2>/dev/null || true \\', '2>/dev/null || true) \\')
    source = replace_once(source, ' \\\n    && rm -rf /var/lib/apt/lists/*', '')
    source = replace_once(source, 'RUN pip install --upgrade pip', '''RUN --mount=type=cache,id=enrui-pip,target=/root/.cache/pip,sharing=locked \\
    export PIP_INDEX_URL=''' + shlex.quote(pip_index_url) + ''' \\
    && pip install --upgrade pip''')
    return source


def atomic_write(path, content):
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n', dir=path.parent, delete=False) as f:
        f.write(content)
        temporary = Path(f.name)
    try:
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    repository = Path(sys.argv[1]).resolve()
    shared = Path(sys.argv[2]).resolve()
    lock_file = Path(sys.argv[4]) if len(sys.argv) > 4 else shared / 'base-images.env'
    images = load_base_images(lock_file)
    source = (repository / 'backend' / 'Dockerfile').read_text(encoding='utf-8')
    source, backend_args = pin_base_images(source, images, {'PYTHON_BASE_IMAGE'})
    frontend_source = (repository / 'frontend' / 'Dockerfile').read_text(encoding='utf-8')
    frontend_source, frontend_args = pin_base_images(frontend_source, images, {'NODE_BASE_IMAGE', 'NGINX_BASE_IMAGE'})
    dockerfile = shared / 'backend.cached.Dockerfile'
    frontend_dockerfile = shared / 'frontend.pinned.Dockerfile'
    pip_index_url = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_PIP_INDEX_URL
    generated = prepare(source, pip_index_url)
    backend_build = {'dockerfile': str(dockerfile)}
    frontend_build = {'dockerfile': str(frontend_dockerfile)}
    if backend_args:
        backend_build['args'] = backend_args
    if frontend_args:
        frontend_build['args'] = frontend_args
    compose = json.dumps({'services': {'backend': {'build': backend_build}, 'frontend': {'build': frontend_build}}}, indent=2) + '\n'
    atomic_write(dockerfile, generated)
    atomic_write(frontend_dockerfile, frontend_source)
    atomic_write(shared / 'docker-compose.build-cache.yml', compose)
    print('已准备 APT 和 pip 下载缓存, 保留 APT 镜像源; PyPI 镜像: ' + pip_index_url)
    for key, value in images.items():
        print('固定基础镜像 ' + key + ': ' + value)


if __name__ == '__main__':
    try:
        main()
    except ValueError as error:
        raise SystemExit('构建配置检查失败: ' + str(error)) from None
