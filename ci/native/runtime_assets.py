# SPDX-FileCopyrightText: 2026 Wisbendji Fimerlus <archledger236@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Stage the pinned runtime QML without changing KWin source or test assertions."""
import argparse
import hashlib
import json
import os
import stat
from pathlib import Path

FRAMES = {
    'frame_none.qml': '688b0eea6dc09e94a75c9148a3886f153897c54560ac9394ae6269a93b176c91',
    'frame_styled.qml': '4ff59f9a7fd98b1434a36d5161415aa26e145294623273a959297b434eeb9b40',
    'frame_unstyled.qml': '6a0e7b693f22abc3f9c8b900291f7565af9669e92713686ebe0f1faa5ebb716d',
}


def stage(source, prefix):
    source, prefix = Path(source).absolute(), Path(prefix).absolute()
    frames = source / 'src/qml/frames/plasma'
    destination = prefix / 'share/kwin-wayland/frames/plasma'
    if frames.resolve() != frames or destination.resolve() != destination:
        raise ValueError('runtime data paths must not contain symlinks')
    verified = {}
    for name, digest in FRAMES.items():
        fd = os.open(frames / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as file:
            if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
                raise ValueError('runtime QML is not a regular file')
            data = file.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024 or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError('runtime QML identity differs: ' + name)
        verified[name] = data
    # Never overlay an existing runtime tree; each test container starts fresh.
    destination.mkdir(parents=True, exist_ok=False)
    records = []
    for name, data in verified.items():
        with (destination / name).open('xb') as output:
            output.write(data)
        records.append({'source': str(frames / name), 'installed': str(destination / name),
                        'sha256': FRAMES[name]})
    return records


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('prefix', type=Path)
    args = parser.parse_args()
    print(json.dumps(stage(args.source, args.prefix), indent=2))
