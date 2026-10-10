"""返事を待つ間の「一声（相槌）」を選ぶ。

OPENROUTER_API_KEY があれば Jev（typesafe/jev-1.13・1回 約$0.00002・0.2〜0.5秒）、
無ければ claude CLI（Haiku）に選ばせる（約0.9秒）。
"""
import json, os, re, subprocess, tempfile, urllib.request

MIN_CONF = 0.45   # これより自信が無い時は相槌を出さない（外れた相槌は無いより悪い）
NONE_DESC = '挨拶・お願い・普通の質問・疑われた時など、どれも自然に当てはまらない時。迷ったらこれ（何も言わずに返事をする）'


def _jev(char, text, exclude, context):
    crit = {k: v['when'] for k, v in char['fillers'].items() if k not in exclude}
    crit['none'] = NONE_DESC
    body = {'model': 'typesafe/jev-1.13',
            'state': {'previous_exchange': context, 'listener_said_now': text, 'speaker': f'{char["name"]}（{char.get("persona_short", "")}）'},
            'questions': {'filler': {'type': 'choice', 'criteria': crit,
                                     'instructions': f'{char["name"]}が返事の前に思わず出す一声として、いちばん自然なものはどれ？'}}}
    req = urllib.request.Request('https://openrouter.ai/api/alpha/decisions', json.dumps(body).encode(),
                                 {'Authorization': 'Bearer ' + os.environ['OPENROUTER_API_KEY'], 'Content-Type': 'application/json'})
    a = json.load(urllib.request.urlopen(req, timeout=10))['answers']['filler']
    conf = a.get('confidence')
    if a['choice'] == 'none' or (conf is not None and conf < MIN_CONF):
        return None
    return a['choice']


def _claude(char, text, exclude, context):
    keys = [k for k in char['fillers'] if k not in exclude]
    sys_prompt = (f'相手の発言を聞いた「{char["name"]}」が、返事の前に思わず出す一声を選ぶ係。'
                  '次のキーから1つだけ、キーだけを答える。どれも合わなければ none。説明は書かない。\n'
                  + '\n'.join(f'{k}={char["fillers"][k]["text"]}（{char["fillers"][k]["when"]}）' for k in keys)
                  + f'\nnone={NONE_DESC}')
    env = {k: v for k, v in os.environ.items()
           if k == 'CLAUDE_CODE_OAUTH_TOKEN' or not (k.startswith('CLAUDE_CODE_') or k in ('CLAUDECODE', 'CLAUDE_PID'))}
    env['CLAUDE_CONFIG_DIR'] = cfg = tempfile.mkdtemp(prefix='voicechat-filler-')
    prompt = (f'直前のやりとり: {context}\n' if context else '') + f'相手の発言: {text}'
    out = subprocess.run(['claude', '-p', '--model', 'haiku', '--system-prompt', sys_prompt, '--tools', '',
                          '--no-session-persistence', '--strict-mcp-config', prompt],
                         capture_output=True, text=True, timeout=30, cwd=cfg, env=env).stdout
    k = re.sub(r'[^a-z]', '', out.lower())
    return k if k in keys else None


def choose(char, text, exclude=(), context=''):
    if not char.get('fillers'):
        return None
    try:
        if os.environ.get('OPENROUTER_API_KEY'):
            return _jev(char, text, exclude, context)
        return _claude(char, text, exclude, context)
    except Exception as e:
        print('相槌選びに失敗（相槌なしで続けます）:', repr(e), flush=True)
        return None
