"""キャラの相槌（character.json の fillers）を、speech.cpp でその声で作って保存する。

  CHARACTER=sample python3 make_fillers.py

声が気に入らない相槌は、そのファイルだけ消してもう一度実行すると作り直す。
"""
import json, os, urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
SPEECH = os.environ.get('SPEECH_URL', 'http://127.0.0.1:8955').rstrip('/')
TTS_MODEL = os.environ.get('TTS_MODEL', 'Irodori-TTS-v4.1-Small-MF')
char_dir = Path(os.environ.get('CHARACTERS_DIR', ROOT / 'characters')) / os.environ.get('CHARACTER', 'sample')
char = json.loads((char_dir / 'character.json').read_text())
out = char_dir / 'fillers'; out.mkdir(exist_ok=True)
for k, v in char['fillers'].items():
    f = out / f'{k}.wav'
    if f.exists():
        continue
    body = json.dumps({'model': TTS_MODEL, 'input': v['text'], 'voice': char['voice'], 'speed': char.get('speed', 1.0)}).encode()
    f.write_bytes(urllib.request.urlopen(urllib.request.Request(SPEECH + '/v1/audio/speech', body,
                                                                {'Content-Type': 'application/json'}), timeout=60).read())
    print('作った:', f.name, v['text'])
print('完了:', out)
