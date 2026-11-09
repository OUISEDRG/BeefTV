"""Select the installed Dreama video channel while BeefTV is closed."""
import base64
import copy
import datetime
import json
import os
import subprocess
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from bridge import MODEL
from install_channel import CHANNEL_ID, decode
from server import ROOT, local_token


def main():
    running = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq BeefTV.exe', '/FO', 'CSV', '/NH'], capture_output=True, check=True)
    if b'beeftv.exe' in running.stdout.lower():
        raise RuntimeError('BeefTV must be closed before changing its configuration.')
    pointer = Path(os.environ['APPDATA']) / 'BeefTV/storage-location.json'
    data = Path(json.loads(pointer.read_text('utf-8-sig'))['dataDir']).resolve()
    target = data / 'local-model-config.json'
    original = target.read_bytes()
    key = (data / '.settings-key').read_bytes()
    document = decode(original, key)
    before = copy.deepcopy(document)
    config = document['config']
    matches = [c for c in config['channels'] if c['id'] == CHANNEL_ID]
    if len(matches) != 1 or not matches[0].get('enabled') or matches[0]['apiKey'] != local_token() or MODEL not in matches[0]['models']:
        raise RuntimeError('Installed Dreama channel does not match the bridge.')
    selected = CHANNEL_ID + '::' + MODEL
    config.update(videoModel=selected, videoSeconds='5', vquality='720', videoGenerateAudio='false')
    for field in ['models', 'videoModels']:
        if selected not in config[field]:
            config[field].append(selected)
    changed = [k for k in config if config[k] != before['config'].get(k)]
    allowed = {'videoModel', 'videoSeconds', 'vquality', 'videoGenerateAudio', 'models', 'videoModels'}
    assert set(changed) <= allowed
    restored = copy.deepcopy(config)
    for field in changed:
        restored[field] = before['config'][field]
    assert restored == before['config']
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = ROOT / 'backups' / ('before-dreama-default-' + stamp + '.json.enc')
    if changed:
        backup.write_bytes(original)
        document['revision'] += 1
        nonce = os.urandom(12)
        encrypted = nonce + AESGCM(key).encrypt(nonce, json.dumps(document, ensure_ascii=False).encode('utf-8'), None)
        output = json.dumps({'schemaVersion': 2, 'encryptedConfig': 'enc:v1:' + base64.b64encode(encrypted).decode().rstrip('=')}).encode()
        assert decode(output, key) == document
        if target.read_bytes() != original:
            raise RuntimeError('Configuration changed concurrently; no write performed.')
        temporary = data / ('.dreama-default-' + stamp + '.tmp')
        with temporary.open('xb') as file:
            file.write(output); file.flush(); os.fsync(file.fileno())
        os.replace(temporary, target)
    assert decode(target.read_bytes(), key) == document
    receipt = {'selectedModel': selected, 'previousVideoModel': before['config']['videoModel'],
               'changedFields': changed, 'durationSeconds': 5, 'resolution': '720P',
               'generationEnabled': False, 'backup': str(backup) if changed else None,
               'revision': document['revision'], 'otherSettingsPreserved': True}
    (ROOT / 'evidence' / 'default-model-replacement.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
