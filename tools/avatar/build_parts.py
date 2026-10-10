"""See-through の出力（1枚の絵を分けたレイヤー）から、会話画面で動かすパーツを作る。

  python3 tools/avatar/build_parts.py <See-throughの出力フォルダ> characters/<名前>/avatar

入力フォルダには See-through が書き出す `src_img.png`（元絵）と、`face.png` `front hair.png` などの
レイヤー（どれも元絵と同じ大きさの透過PNG）がある前提。

やること
- 止まっている時の見た目が元絵と完全に同じになるよう、各画素を「一番手前のレイヤー」に持たせ、元絵の色を入れる
  （See-through が描き足した絵は、手前の層に隠れていた所にだけ残る＝動かした時に下から見える）
- 腕は左右に分ける（See-through は両腕を1枚にする）
- 頭・肩・腰の回転の中心（pivot）を、レイヤーの位置から決める
- 頬の赤みを塗ってよい所（頬のあたりの肌色）を blushmask.png にする
- 最後に全部重ねて、元絵とどれだけ違うかを表示する
"""
import json, sys
from pathlib import Path
import numpy as np
from PIL import Image, ImageFilter

SRC, OUT = Path(sys.argv[1]), Path(sys.argv[2])
(OUT / 'parts').mkdir(parents=True, exist_ok=True)

# 奥 → 手前。無いレイヤーは飛ばす
ORDER = ['back hair', 'tail', 'wings', 'legwear', 'footwear', 'bottomwear', 'arm_l', 'arm_r', 'neck', 'topwear',
         'neckwear', 'ears', 'earwear', 'face', 'mouth', 'eyewhite', 'irides', 'eyelash', 'eyebrow', 'eyewear',
         'front hair', 'headwear', 'objects']
src = np.array(Image.open(SRC / 'src_img.png').convert('RGBA'))
H, W = src.shape[:2]
xx = np.arange(W)[None, :]


def bbox(a):
    ys, xs = np.nonzero(a[..., 3] > 24)
    return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1) if len(xs) else None


def load(n, face_cx):
    if n in ('arm_l', 'arm_r'):
        f = SRC / 'handwear.png'
        if not f.exists():
            return None
        a = np.array(Image.open(f).convert('RGBA'))
        a[..., 3] = np.where((xx < face_cx) if n == 'arm_l' else (xx >= face_cx), a[..., 3], 0)
    else:
        f = SRC / f'{n}.png'
        if not f.exists():
            return None
        a = np.array(Image.open(f).convert('RGBA'))
    a[..., 3] = np.where(a[..., 3] < 24, 0, a[..., 3])     # 薄いゴミを消す
    return a if a[..., 3].any() else None


face = np.array(Image.open(SRC / 'face.png').convert('RGBA'))
fb = bbox(face)
face_cx = (fb[0] + fb[2]) / 2
L = {}
for n in ORDER:
    a = load(n, face_cx)
    if a is not None:
        L[n] = a
order = [n for n in ORDER if n in L]

# 各画素を一番手前のレイヤーに持たせる。縁の半透明を下の層に残すと、動かした時に元絵の線が二重に出る。
# 前髪は顔全体にうっすら乗るので、前髪だけは濃い所しか持たせない（目を閉じても目が残るのを防ぐ）
fg = np.stack([L[n][..., 3] for n in order]).max(0) > 24
top = np.full((H, W), -1)
for i, n in enumerate(order):
    top[L[n][..., 3] > (128 if n == 'front hair' else 24)] = i
none = fg & (top < 0)
if none.any():
    top[none] = np.stack([L[n][..., 3] for n in order])[:, none].argmax(0)

parts = {}
for i, n in enumerate(order):
    a = L[n]; m = (top == i) & fg
    a[m, :3] = src[m, :3]; a[m, 3] = 255
    im = Image.fromarray(a); bb = im.getbbox()
    im.crop(bb).save(OUT / 'parts' / f'{n}.png')
    parts[n] = {'x': bb[0], 'y': bb[1], 'w': bb[2] - bb[0], 'h': bb[3] - bb[1]}


def bb_of(n):
    q = parts.get(n)
    return (q['x'], q['y'], q['x'] + q['w'], q['y'] + q['h']) if q else None


neck = bb_of('neck') or (fb[0], fb[3] - 20, fb[2], fb[3])
top_b = bb_of('topwear') or (fb[0] - 100, fb[3], fb[2] + 100, fb[3] + 400)
pivots = {
    'head': [face_cx, (neck[1] + neck[3]) / 2],                    # 首のまんなか
    'waist': [face_cx, top_b[3]],                                   # 上着のすそ
    'sh_l': [top_b[0] + (top_b[2] - top_b[0]) * 0.1, top_b[1] + (top_b[3] - top_b[1]) * 0.15],
    'sh_r': [top_b[2] - (top_b[2] - top_b[0]) * 0.1, top_b[1] + (top_b[3] - top_b[1]) * 0.15],
}

# 頬の赤みを塗ってよい所：頬の位置で、元絵が肌色の画素（目・口・眉には塗らない）
eyes = [bb_of(n) for n in ('eyewhite', 'irides', 'eyelash') if bb_of(n)]
blush = None
if eyes:
    ex0, ey0, ex1, ey1 = min(b[0] for b in eyes), min(b[1] for b in eyes), max(b[2] for b in eyes), max(b[3] for b in eyes)
    cheek_y = ey0 + (ey1 - ey0) * 0.85      # 目の下の端あたり
    cheeks_at = [[ex0 + (ex1 - ex0) * 0.2, cheek_y], [ex0 + (ex1 - ex0) * 0.8, cheek_y]]
    rgb = src[..., :3].astype(int); r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    skin = (r > 205) & (g > 165) & (b > 150) & (r - b > 12) & (r - b < 70)
    yy, x2 = np.mgrid[0:H, 0:W]; rx, ry = (ex1 - ex0) * 0.3, (ey1 - ey0) * 0.9
    cheeks = np.zeros((H, W), bool)
    for cx, cy in cheeks_at:
        cheeks |= ((x2 - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2 < 1
    for n in ('eyewhite', 'irides', 'eyelash', 'eyebrow', 'mouth'):
        if n in L:
            cheeks &= ~(L[n][..., 3] > 24)
    mk = Image.fromarray(((skin & cheeks) * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(3))
    bb = mk.getbbox()
    if bb:
        rgba = Image.new('RGBA', mk.size, (255, 111, 140, 0)); rgba.putalpha(mk)
        rgba.crop(bb).save(OUT / 'blushmask.png')
        blush = {'x': bb[0], 'y': bb[1], 'w': bb[2] - bb[0], 'h': bb[3] - bb[1], 'cheeks': cheeks_at}

meta = {'W': W, 'H': H, 'order': order, 'parts': parts, 'pivots': pivots, 'blush': blush, 'expr': {}}
old = OUT / 'parts.json'
if old.exists():                       # 作り直しても、表情差分の登録は残す
    meta['expr'] = json.loads(old.read_text()).get('expr', {})
old.write_text(json.dumps(meta, ensure_ascii=False, indent=1))

# 検算：全部重ねて元絵と比べる
comp = Image.new('RGBA', (W, H), (255, 255, 255, 255))
for n in order:
    q = parts[n]; comp.alpha_composite(Image.open(OUT / 'parts' / f'{n}.png'), (q['x'], q['y']))
bg = Image.new('RGBA', (W, H), (255, 255, 255, 255)); bg.alpha_composite(Image.fromarray(src))
d = np.abs(np.array(comp.convert('RGB')).astype(int) - np.array(bg.convert('RGB')).astype(int)).max(2)
inner = d[:, 2:-2][2:-2]          # 正方形にする時に足された余白の境目(1〜2px)は数えない
print(f'パーツ {len(order)} 枚 → {OUT}  元絵と違う画素(差>40): {int((d > 40).sum())}（うち頭まわり: '
      f'{int((d[max(0, fb[1] - 60):fb[3] + 40, max(0, fb[0] - 80):fb[2] + 80] > 40).sum())}）')
