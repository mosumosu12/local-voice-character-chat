"""表情の差分（閉じ目・笑い目・開いた口）を作って、パーツに載せる。

目や口を「押しつぶして閉じる」と、高さがずれたり半目になったりして不自然になる。
なので、元絵の目・口のところだけを画像生成AI（SD WebUI / Forge の API）で描き直した「差分の絵」に差し替える。
マスクの外は元絵と同じなので、差し替えても継ぎ目が出ない。

1) 候補を作る（目・口の範囲は parts.json から自動で決まる）
  python3 tools/avatar/make_expressions.py gen <See-throughの出力フォルダ> characters/<名前>/avatar \
      --api http://127.0.0.1:7860 --prompt "1girl, black hair, ..." [--seeds 1 2 3]
  → characters/<名前>/avatar/expr_drafts/ に eyes_closed_s1.png などができる

2) 気に入った1枚を登録する
  python3 tools/avatar/make_expressions.py use characters/<名前>/avatar eyes_closed=expr_drafts/eyes_closed_s2.png ...

ポイント
- キャラの外見を書いたプロンプトに「ぱっちりした目」などの開いた目を呼ぶ指定があると、閉じ目を描かせても開いたままになる。
  目・口の小さな範囲だけ描き直すので外見は周りから引き継がれる → 外見の指定は最小限でよい（髪色くらい）
- 打ち消しの言葉（〜しない）を足すより、開いた目を呼ぶ指定を消す方が効く
"""
import argparse, base64, io, json, sys, urllib.request
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

EXPR = {
    'eyes_closed': ('eyes', 'closed eyes, smile'),
    'eyes_happy': ('eyes', 'closed eyes, ^_^, happy, smile, blush'),
    'mouth_open': ('mouth', 'open mouth, smile'),
}


def regions(meta):
    """目・口の範囲（楕円の外枠）。レイヤーの位置から余白を付けて決める"""
    P = meta['parts']

    def bb(names, pad_x, pad_y):
        bs = [(P[n]['x'], P[n]['y'], P[n]['x'] + P[n]['w'], P[n]['y'] + P[n]['h']) for n in names if n in P]
        if not bs:
            return None
        x0, y0, x1, y1 = min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)
        w, h = x1 - x0, y1 - y0
        return [x0 - w * pad_x, y0 - h * pad_y, x1 + w * pad_x, y1 + h * pad_y]
    eyes = bb(['eyewhite', 'irides', 'eyelash'], 0.06, 0.25)
    out = {}
    if eyes:        # 左右の目を別々の楕円にする（真ん中の鼻筋を描き直さない）
        cx = (eyes[0] + eyes[2]) / 2; gap = (eyes[2] - eyes[0]) * 0.04
        out['eyes'] = [[eyes[0], eyes[1], cx - gap, eyes[3]], [cx + gap, eyes[1], eyes[2], eyes[3]]]
    mouth = bb(['mouth'], 0.6, 1.2)
    if mouth:
        out['mouth'] = [mouth]
    return out


def mask_of(size, ellipses, shrink=0, blur=0):
    m = Image.new('L', size, 0); d = ImageDraw.Draw(m)
    for x0, y0, x1, y1 in ellipses:
        d.ellipse((x0 + shrink, y0 + shrink, x1 - shrink, y1 - shrink), fill=255)
    return m.filter(ImageFilter.GaussianBlur(blur)) if blur else m


def b64(im):
    b = io.BytesIO(); im.save(b, 'PNG'); return base64.b64encode(b.getvalue()).decode()


def gen(a):
    out = Path(a.avatar); meta = json.loads((out / 'parts.json').read_text())
    src = Image.open(Path(a.seethrough) / 'src_img.png').convert('RGB')
    R = regions(meta); drafts = out / 'expr_drafts'; drafts.mkdir(exist_ok=True)
    for name in a.only or EXPR:
        region, tags = EXPR[name]
        if region not in R:
            print('範囲が見つからないので飛ばす:', name); continue
        for seed in a.seeds:
            body = {'init_images': [b64(src)], 'mask': b64(mask_of(src.size, R[region])),
                    'prompt': f'{a.prompt}, {tags}', 'negative_prompt': a.negative,
                    'denoising_strength': 0.85, 'inpainting_fill': 1, 'inpaint_full_res': True, 'inpaint_full_res_padding': 64,
                    'mask_blur': 6, 'steps': 30, 'cfg_scale': 6, 'seed': seed, 'width': src.width, 'height': src.height}
            req = urllib.request.Request(a.api.rstrip('/') + '/sdapi/v1/img2img', json.dumps(body).encode(),
                                         {'Content-Type': 'application/json'})
            r = json.load(urllib.request.urlopen(req, timeout=900))
            p = drafts / f'{name}_s{seed}.png'
            Image.open(io.BytesIO(base64.b64decode(r['images'][0]))).convert('RGB').save(p)
            (drafts / f'{p.name}.json').write_text(json.dumps({'region': R[region], 'tags': tags, 'seed': seed}, ensure_ascii=False))
            print('作った:', p)
    print('気に入った物を `use` で登録してください')


def use(a):
    out = Path(a.avatar); meta = json.loads((out / 'parts.json').read_text())
    for arg in a.pairs:
        name, rel = arg.split('=', 1); path = out / rel if not Path(rel).is_absolute() else Path(rel)
        info = json.loads(Path(str(path) + '.json').read_text())
        var = Image.open(path).convert('RGBA')
        # 描き直した楕円を少し内側にしてぼかす。外側は元絵と同じなので境目は出ない
        var.putalpha(mask_of(var.size, info['region'], shrink=2, blur=4))
        bb = var.getbbox(); var.crop(bb).save(out / 'parts' / f'{name}.png')
        meta['expr'][name] = {'x': bb[0], 'y': bb[1], 'w': bb[2] - bb[0], 'h': bb[3] - bb[1]}
        print('登録:', name, meta['expr'][name])
    (out / 'parts.json').write_text(json.dumps(meta, ensure_ascii=False, indent=1))


ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest='cmd', required=True)
g = sub.add_parser('gen'); g.add_argument('seethrough'); g.add_argument('avatar')
g.add_argument('--api', default='http://127.0.0.1:7860'); g.add_argument('--prompt', default='1girl')
g.add_argument('--negative', default='worst quality, low quality'); g.add_argument('--seeds', type=int, nargs='+', default=[1, 2, 3])
g.add_argument('--only', nargs='+', choices=list(EXPR))
u = sub.add_parser('use'); u.add_argument('avatar'); u.add_argument('pairs', nargs='+')
a = ap.parse_args()
gen(a) if a.cmd == 'gen' else use(a)
