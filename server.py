"""AIキャラと声で会話するサーバー（ローカルGPU + Claude）

ブラウザで話す → 書き起こし(speech.cpp) → 相槌を先に返す → 返事(Claude) → キャラの声(speech.cpp)

  python3 server.py [port] [--https]

設定は環境変数（README 参照）。キャラは characters/<名前>/character.json。
"""
import atexit, base64, json, os, re, shutil, subprocess, sys, tempfile, threading, time, urllib.parse, urllib.request, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import fillers

ROOT = Path(__file__).parent
SPEECH = os.environ.get('SPEECH_URL', 'http://127.0.0.1:8955').rstrip('/')
ASR_MODEL = os.environ.get('ASR_MODEL', 'Qwen3-ASR-1.7B')
TTS_MODEL = os.environ.get('TTS_MODEL', 'Irodori-TTS-v4.1-Small-MF')
CHAR_DIR = Path(os.environ.get('CHARACTERS_DIR', ROOT / 'characters')) / os.environ.get('CHARACTER', 'sample')
CHAR = json.loads((CHAR_DIR / 'character.json').read_text())
AVATAR_DIR = CHAR_DIR / 'avatar'      # tools/avatar/ で作ったキャラの絵（無ければ文字だけの画面）
LOG = ROOT / 'talk_log.jsonl'
MAX_BODY = 25 * 1024 * 1024   # 1回に受け取る音声の上限
MAX_SESSIONS = 8              # 同時に持つ会話の数（超えたら古いものから閉じる）

VOICE_NOTE = ('\n\n## これは1対1の音声通話\n'
              '- 相手の言葉は音声認識で文字にしたもの。聞き間違いがあっても意味を汲んで自然に答える\n'
              '- 返事は話し言葉で1〜2文・50文字くらいまで。絵文字・記号・括弧・顔文字・英字は使わない\n')


def clean_for_voice(t):
    """声に流すので、改行・タグ・括弧書き・記号を落とす"""
    t = re.sub(r'</?[a-zA-Z_:][^>]*>', '', t or '').replace('\n', ' ')
    t = re.sub(r'[`*#＊＃]|（[^）]*）|\([^)]*\)', '', t)
    return re.sub(r'\s+', ' ', t).strip()


class CliBrain:
    """claude CLI を1本立てっぱなしにして会話を続ける（毎回起動しないので速い）。
    Claude のサブスクで動く。CLAUDE_CODE_OAUTH_TOKEN（`claude setup-token` で発行）を使う。"""
    def __init__(self, system, model):
        self.system, self.model = system, model
        self.lock = threading.Lock(); self.p = None
        self.cfg = tempfile.mkdtemp(prefix='voicechat-claude-')
        atexit.register(shutil.rmtree, self.cfg, True)

    def _start(self):
        env = {k: v for k, v in os.environ.items()
               if k == 'CLAUDE_CODE_OAUTH_TOKEN' or not (k.startswith('CLAUDE_CODE_') or k in ('CLAUDECODE', 'CLAUDE_PID'))}
        # 普段の ~/.claude（CLAUDE.md・MCP 等）が会話に混ざらないよう、空の設定フォルダで起動する
        env['CLAUDE_CONFIG_DIR'] = self.cfg
        self.p = subprocess.Popen(['claude', '-p', '--model', self.model, '--input-format', 'stream-json',
                                   '--output-format', 'stream-json', '--verbose', '--system-prompt', self.system,
                                   '--tools', '', '--no-session-persistence', '--strict-mcp-config'],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  text=True, bufsize=1, cwd=self.cfg, env=env)

    def ask(self, text):
        with self.lock:
            if self.p is None or self.p.poll() is not None:
                self._start()
            self.p.stdin.write(json.dumps({'type': 'user', 'message': {'role': 'user', 'content': text}}) + '\n')
            self.p.stdin.flush()
            while True:
                line = self.p.stdout.readline()
                if not line:
                    self.p = None
                    return CHAR.get('error_line', 'ごめんね、もう一回言って？')
                d = json.loads(line)
                if d.get('type') == 'result':
                    if d.get('is_error'):
                        # 未ログイン等。エラー文をキャラの声で読み上げないように
                        print('claude CLI エラー:', d.get('result'), '（CLAUDE_CODE_OAUTH_TOKEN を確認）', flush=True)
                        return CHAR.get('error_line', 'ごめんね、もう一回言って？')
                    return clean_for_voice(d.get('result'))

    def reset(self):
        with self.lock:
            if self.p:
                self.p.kill()
            self.p = None


class ApiBrain:
    """ANTHROPIC_API_KEY がある時は API を直接呼ぶ（従量課金）。会話履歴はこちらで持つ。"""
    def __init__(self, system, model):
        self.system, self.model = system, model
        self.lock = threading.Lock(); self.msgs = []

    def ask(self, text):
        with self.lock:
            self.msgs.append({'role': 'user', 'content': text})
            while len(self.msgs) > 40:      # 古いやりとりを1往復ずつ落とす（先頭が必ず user になるように）
                del self.msgs[:2]
            body = {'model': self.model, 'max_tokens': 1024, 'messages': self.msgs,
                    'system': [{'type': 'text', 'text': self.system, 'cache_control': {'type': 'ephemeral'}}]}
            req = urllib.request.Request('https://api.anthropic.com/v1/messages', json.dumps(body).encode(),
                                         {'x-api-key': os.environ['ANTHROPIC_API_KEY'], 'anthropic-version': '2023-06-01',
                                          'content-type': 'application/json'})
            try:
                r = json.load(urllib.request.urlopen(req, timeout=60))
            except Exception:
                self.msgs.pop()             # 失敗した発言は履歴に残さない
                raise
            reply = ''.join(c.get('text', '') for c in r.get('content', []))
            self.msgs.append({'role': 'assistant', 'content': reply})
            return clean_for_voice(reply)

    def reset(self):
        with self.lock:
            self.msgs = []


def make_brain(system, cli_model, api_model):
    if os.environ.get('ANTHROPIC_API_KEY'):
        return ApiBrain(system, os.environ.get('API_MODEL', api_model))
    return CliBrain(system, os.environ.get('CLI_MODEL', cli_model))


SYSTEM = CHAR['system_prompt'] + VOICE_NOTE
brains = {}         # 会話(sid)ごとの返事役
LAST_FILLER = {}    # 会話ごとの直前の相槌（同じのを続けて出さない）
LAST_TURN = {}      # 会話ごとの直前のやりとり（相槌選びの文脈）
JOBS = {}


brains_lock = threading.Lock()


def get_brain(sid):
    with brains_lock:
        if sid not in brains:
            while len(brains) >= MAX_SESSIONS:     # 古い会話を閉じる（claude のプロセスが増え続けないように）
                old = next(iter(brains)); b = brains.pop(old)
                if hasattr(b, 'reset'):
                    b.reset()
            brains[sid] = make_brain(SYSTEM, 'sonnet', 'claude-sonnet-5-5')
        return brains[sid]


FILLER_AUDIO = {}
for k in CHAR.get('fillers', {}):
    f = CHAR_DIR / 'fillers' / f'{k}.wav'
    if f.exists():
        FILLER_AUDIO[k] = base64.b64encode(f.read_bytes()).decode()
if CHAR.get('fillers') and not FILLER_AUDIO:
    print('相槌の声がまだありません → python3 make_fillers.py で作ってください（無くても会話はできます）', flush=True)


def asr(wav_bytes):
    b = uuid.uuid4().hex
    body = (f'--{b}\r\nContent-Disposition: form-data; name="model"\r\n\r\n{ASR_MODEL}\r\n'
            f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\nContent-Type: audio/wav\r\n\r\n'
            ).encode() + wav_bytes + f'\r\n--{b}--\r\n'.encode()
    r = urllib.request.urlopen(urllib.request.Request(SPEECH + '/v1/audio/transcriptions', body,
                                                      {'Content-Type': f'multipart/form-data; boundary={b}'}), timeout=60)
    return json.load(r).get('text', '').strip()


def tts(text):
    # 「。」で区切ると1文ずつ作られて間が空くので、「、」でつないで最後だけ「。」にする
    one = re.sub('。', '、', text).rstrip('、') + '。' if text else text
    body = json.dumps({'model': TTS_MODEL, 'input': one, 'voice': CHAR['voice'], 'speed': CHAR.get('speed', 1.0)}).encode()
    return urllib.request.urlopen(urllib.request.Request(SPEECH + '/v1/audio/speech', body,
                                                         {'Content-Type': 'application/json'}), timeout=60).read()


def to_wav16k(raw):
    # パイプに書くと WAV の長さ欄が空になり speech.cpp が 400 を返すので、いったんファイルに書く
    with tempfile.TemporaryDirectory() as d:
        src, dst = os.path.join(d, 'in.bin'), os.path.join(d, 'out.wav')
        open(src, 'wb').write(raw)
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', src, '-ac', '1', '-ar', '16000', dst], check=True)
        return open(dst, 'rb').read()


def _finish(job, sid, heard, t, s0, said):
    try:
        msg = heard or '（何も聞き取れなかった）'
        if said:
            msg += f'\n（あなたはこの返事の前に、もう「{said}」と一声出している。その続きとして話し、「{said}」や同じ相槌で始めない）'
        s2 = time.time(); reply = get_brain(sid).ask(msg); t['返事'] = time.time() - s2
        s3 = time.time(); audio = tts(reply); t['声'] = time.time() - s3
        t['本返事まで'] = time.time() - s0
        LAST_TURN[sid] = f'相手「{heard}」→{CHAR["name"]}「{reply}」'
        job.update(reply=reply, audio=base64.b64encode(audio).decode(), sec={k: round(v, 2) for k, v in t.items()})
        with open(LOG, 'a') as f:
            f.write(json.dumps({'at': time.strftime('%F %T'), 'heard': heard, 'filler': job.get('filler'),
                                'reply': reply, 'sec': job['sec']}, ensure_ascii=False) + '\n')
    except Exception as e:
        job['error'] = str(e)
    job['done'] = True


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ct):
        self.send_response(code); self.send_header('Content-Type', ct); self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store'); self.end_headers(); self.wfile.write(body)

    def sid(self):
        return (re.search(r'sid=([A-Za-z0-9_-]+)', self.path) or [None, 'default'])[1]

    def do_GET(self):
        p = self.path.split('?')[0]
        if p in ('/', '/index.html'):
            return self.send(200, (ROOT / 'index.html').read_bytes(), 'text/html; charset=utf-8')
        if p == '/config':
            cfg = {'name': CHAR['name'], 'theme': CHAR.get('theme', {}), 'avatar': AVATAR_DIR.joinpath('parts.json').exists(),
                   'smile_words': CHAR.get('smile_words', 'えへへ|やった|わぁ|ふふ|あはは|うれし|楽し|好き')}
            return self.send(200, json.dumps(cfg, ensure_ascii=False).encode(), 'application/json')
        if p == '/avatar.js':
            return self.send(200, (ROOT / 'avatar.js').read_bytes(), 'text/javascript; charset=utf-8')
        if p.startswith('/avatar/'):      # キャラの絵（characters/<名前>/avatar/ の中だけ）
            f = (AVATAR_DIR / urllib.parse.unquote(p[len('/avatar/'):])).resolve()
            if AVATAR_DIR.resolve() in f.parents and f.is_file():
                ct = 'application/json' if f.suffix == '.json' else 'image/png'
                return self.send(200, f.read_bytes(), ct)
            return self.send(404, b'', 'text/plain')
        if p == '/reply':
            jid = self.path.split('id=')[-1]
            for _ in range(2400):     # 最大2分待つ
                j = JOBS.get(jid)
                if j is None or j.get('done'):
                    break
                time.sleep(0.05)
            j = JOBS.pop(jid, None) or {'error': 'no job'}
            if not j.get('done') and 'error' not in j:
                j = {'error': '返事が2分以内に作れませんでした'}
            return self.send(200, json.dumps(j, ensure_ascii=False).encode(), 'application/json')
        if p == '/reset':
            sid = self.sid()
            if sid in brains:
                brains[sid].reset()
            LAST_TURN.pop(sid, None); LAST_FILLER.pop(sid, None)
            return self.send(200, b'{"ok":true}', 'application/json')
        self.send(404, b'', 'text/plain')

    def do_POST(self):
        n = int(self.headers.get('Content-Length', 0))
        if n > MAX_BODY:
            return self.send(413, json.dumps({'error': '音声が長すぎます'}, ensure_ascii=False).encode(), 'application/json')
        raw = self.rfile.read(n)
        sid = self.sid(); t = {}; s = time.time()
        try:
            if self.path.startswith('/talk'):
                wav = to_wav16k(raw); s1 = time.time(); heard = asr(wav); t['書き起こし'] = time.time() - s1
            elif self.path.startswith('/text'):    # 文字で話しかける（マイクが使えない時用）
                heard = json.loads(raw)['text']
            else:
                return self.send(404, b'', 'text/plain')
            use_filler = 'filler=0' not in self.path and FILLER_AUDIO
            fk = None
            if use_filler:
                s4 = time.time()
                fk = fillers.choose(CHAR, heard, exclude=(LAST_FILLER.get(sid),), context=LAST_TURN.get(sid, ''))
                if fk not in FILLER_AUDIO:
                    fk = None
                if fk:
                    LAST_FILLER[sid] = fk
                t['相槌選び'] = time.time() - s4
            t['相槌まで'] = time.time() - s
            jid = uuid.uuid4().hex; job = {'heard': heard, 'filler': fk}; JOBS[jid] = job
            said = CHAR['fillers'][fk]['text'] if fk else ''
            threading.Thread(target=_finish, args=(job, sid, heard, dict(t), s, said), daemon=True).start()
            out = {'job': jid, 'heard': heard, 'filler': said, 'filler_audio': FILLER_AUDIO.get(fk, ''),
                   'sec': {k: round(v, 2) for k, v in t.items()}}
            return self.send(200, json.dumps(out, ensure_ascii=False).encode(), 'application/json')
        except Exception as e:
            return self.send(500, json.dumps({'error': str(e)}, ensure_ascii=False).encode(), 'application/json')


if __name__ == '__main__':
    argv = sys.argv[1:]
    host = '127.0.0.1'      # 既定はこのPCからだけ。スマホから使う時は --host 0.0.0.0（同じLAN内だけで使うこと）
    if '--host' in argv:
        i = argv.index('--host'); host = argv[i + 1]; del argv[i:i + 2]
    args = [a for a in argv if not a.startswith('--')]
    port = int(args[0]) if args else 8960
    srv = ThreadingHTTPServer((host, port), H)
    scheme = 'http'
    if '--https' in sys.argv:   # スマホのマイクは https でないと使えない（自己署名でOK・初回だけ警告が出る）
        import ssl
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(ROOT / 'cert.pem', ROOT / 'key.pem')
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True); scheme = 'https'
    mode = 'API' if os.environ.get('ANTHROPIC_API_KEY') else 'claude CLI'
    print(f'{CHAR["name"]} と話す: {scheme}://localhost:{port}/  （待受: {host} / 返事: {mode} / 声: {SPEECH}）', flush=True)
    if mode == 'claude CLI' and not os.environ.get('CLAUDE_CODE_OAUTH_TOKEN'):
        print('⚠ CLAUDE_CODE_OAUTH_TOKEN が未設定です。`claude setup-token` で発行して設定してください', flush=True)
    srv.serve_forever()
