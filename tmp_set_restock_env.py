from pathlib import Path

service_path = Path('/etc/systemd/system/tienda.service')
line_wanted = 'Environment=PINCENTRAL_RESTOCK_BETWEEN_PINS_SECONDS=2\n'

text = service_path.read_text(encoding='utf-8')
lines = text.splitlines(keepends=True)

found = False
for i, ln in enumerate(lines):
    if ln.startswith('Environment=PINCENTRAL_RESTOCK_BETWEEN_PINS_SECONDS='):
        lines[i] = line_wanted
        found = True
        break

if not found:
    insert_at = None
    for i, ln in enumerate(lines):
        if ln.startswith('Environment=PINCENTRAL_VERIFY_SSL='):
            insert_at = i + 1
            break
    if insert_at is None:
        for i, ln in enumerate(lines):
            if ln.startswith('ExecStart='):
                insert_at = i
                break
    if insert_at is None:
        insert_at = len(lines)
    lines.insert(insert_at, line_wanted)

new_text = ''.join(lines)
if new_text != text:
    service_path.write_text(new_text, encoding='utf-8')
    print('updated')
else:
    print('unchanged')
