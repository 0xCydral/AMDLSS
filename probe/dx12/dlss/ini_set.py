"""Sets key=value inside one [section] of an ini file, keeping every other line (OptiScaler.ini repeats key names like
Enabled across sections, so a plain search-and-replace is not safe).
Run: python3 -I probe/dx12/dlss/ini_set.py <file> <section> <key> <value>"""
import sys


def set_value(text, section, key, value):
    lines = text.splitlines(keepends=True)
    inside = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('[') and stripped.endswith(']'):
            inside = stripped[1:-1] == section
            continue
        if inside and stripped.split('=', 1)[0] == key and '=' in stripped:
            ending = line[len(line.rstrip('\r\n')):]
            lines[i] = f'{key}={value}{ending}'
            return ''.join(lines)
    raise SystemExit(f'{key} not found in [{section}]')


if __name__ == '__main__':
    path, section, key, value = sys.argv[1:5]
    with open(path, encoding='utf-8', newline='') as f:
        text = f.read()
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(set_value(text, section, key, value))
