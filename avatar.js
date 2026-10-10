// パーツ分けしたキャラを canvas で動かす（呼吸・まばたき・頭の揺れ・口パク・表情差分）。
// parts.json は tools/avatar/build_parts.py が作る。表情差分は tools/avatar/make_expressions.py で載せる。
//   const av = await Avatar.load(canvas, '/avatar/');
//   av.mouth = 0..1（声の大きさ）   av.mood = 'smile' | 'neutral'
const Avatar = {
  async load(cv, base) {
    const P = await (await fetch(base + 'parts.json', { cache: 'no-store' })).json();
    const img = {};
    const loadImg = (k, src) => new Promise(ok => { const i = new Image(); i.onload = ok; i.onerror = ok; i.src = src; img[k] = i; });
    await Promise.all([...P.order.map(n => loadImg(n, base + 'parts/' + encodeURIComponent(n) + '.png')),
      ...Object.keys(P.expr || {}).map(k => loadImg(k, base + 'parts/' + k + '.png')),
      P.blush ? loadImg('__blush', base + 'blushmask.png') : null]);
    cv.width = P.W; cv.height = P.H;
    const g = cv.getContext('2d'), PV = P.pivots, EX = P.expr || {};
    const HEAD = new Set(['ears', 'earwear', 'face', 'mouth', 'eyewhite', 'irides', 'eyelash', 'eyebrow', 'eyewear', 'front hair', 'headwear', 'back hair', 'tail']);
    const EYE = new Set(['eyewhite', 'irides', 'eyelash']);
    const faceCv = document.createElement('canvas');
    const st = { t: 0, headA: 0, headV: 0, nod: 0, nodV: 0, blinkAt: 2, blinkStart: -9, blush: 0, mouthS: 0 };
    const av = { mouth: 0, mood: 'neutral', nod() { st.nodV -= 0.04; } };
    const spring = (x, v, tg, k, d, dt) => { v += (-k * (x - tg) - d * v) * dt; return [x + v * dt, v]; };
    const rot = (c, a) => { g.translate(c[0], c[1]); g.rotate(a); g.translate(-c[0], -c[1]); };
    const put = (n) => { const q = P.parts[n]; g.drawImage(img[n], q.x, q.y); };
    const putEx = (k) => { const q = EX[k]; g.drawImage(img[k], q.x, q.y); };
    let prev = performance.now();
    function loop(now) {
      const dt = Math.min(0.05, (now - prev) / 1000); prev = now; st.t += dt; const t = st.t;
      const smile = av.mood === 'smile';
      [st.headA, st.headV] = spring(st.headA, st.headV, Math.sin(t * 0.7) * 0.015 + (smile ? 0.03 : 0), 30, 6, dt);
      [st.nod, st.nodV] = spring(st.nod, st.nodV, 0, 25, 6, dt);
      st.blush += ((smile ? 1 : 0) - st.blush) * dt * 2;
      // 口：声の大きさを少しなめらかにして、ある程度以上で「開いた口」の差分に差し替える
      st.mouthS += (av.mouth - st.mouthS) * Math.min(1, dt * 25);
      if (t >= st.blinkAt) { st.blinkStart = t; st.blinkAt = t + 2.5 + Math.random() * 3; }
      const blink = Math.abs(t - st.blinkStart - 0.07) < 0.07;
      const breath = Math.sin(t * 1.6), bodyY = breath * 2.4, armA = breath * 0.012;
      g.clearRect(0, 0, P.W, P.H);
      const headTf = () => { g.translate(0, bodyY + st.nod * 32); rot(PV.head, st.headA); };
      let eyesDone = false;
      for (const n of P.order) {
        g.save();
        if (n === 'arm_l') { g.translate(0, bodyY); rot(PV.sh_l, armA); put(n); }
        else if (n === 'arm_r') { g.translate(0, bodyY); rot(PV.sh_r, -armA); put(n); }
        else if (n === 'neck' || n === 'topwear' || n === 'neckwear') {
          g.translate(0, bodyY); g.translate(PV.waist[0], PV.waist[1]); g.scale(1, 1 + breath * 0.004); g.translate(-PV.waist[0], -PV.waist[1]); put(n);
        }
        else if (n === 'face') {
          headTf(); put(n);
          // 頬の赤み：元絵で肌色だった所（blushmask）にだけ、ふんわり塗る。笑い目の差分に赤みが描いてある時は足さない
          if (P.blush && st.blush > 0.02 && !(smile && EX.eyes_happy)) {
            const q = P.blush; faceCv.width = q.w; faceCv.height = q.h; const f = faceCv.getContext('2d');
            for (const [cx, cy] of q.cheeks) {
              const x = cx - q.x, y = cy - q.y, r = q.w * 0.22, gr = f.createRadialGradient(x, y, 0, x, y, r);
              gr.addColorStop(0, 'rgba(255,111,140,1)'); gr.addColorStop(1, 'rgba(255,111,140,0)'); f.fillStyle = gr; f.beginPath(); f.arc(x, y, r, 0, 7); f.fill();
            }
            f.globalCompositeOperation = 'destination-in'; f.drawImage(img.__blush, 0, 0);
            g.globalAlpha = st.blush * 0.4; g.drawImage(faceCv, q.x, q.y); g.globalAlpha = 1;
          }
        }
        else if (n === 'mouth') { headTf(); if (st.mouthS > 0.18 && EX.mouth_open) putEx('mouth_open'); else put(n); }
        else if (EYE.has(n)) {
          if (!eyesDone) {
            eyesDone = true; headTf();
            const shut = smile && EX.eyes_happy ? 'eyes_happy' : (blink && EX.eyes_closed ? 'eyes_closed' : null);
            if (shut) putEx(shut); else { if (P.parts.eyewhite) put('eyewhite'); if (P.parts.irides) put('irides'); if (P.parts.eyelash) put('eyelash'); }
          }
        }
        else if (HEAD.has(n)) { headTf(); put(n); }
        else put(n);
        g.restore();
      }
      requestAnimationFrame(loop);
    }
    requestAnimationFrame(loop);
    return av;
  },
};
