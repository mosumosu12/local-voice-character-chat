// ハンズフリー：ボタンを押さずに、話しかけるだけで送る。
//   話し始め … 声がしばらく続いたら録り始める（少し前から録っておくので、出だしが切れない）
//   話し終わり … 黙って 1.2 秒たったら送る（文と文の間の息つぎ 0.9 秒くらいでは切らない）
//   キャラが話している間・返事を待っている間は聞かない（キャラの声を拾って会話がループしないように）
//
//   const hf = await HandsFree.start({ onSpeech: async (wavBlob) => {...}, isBusy: () => bool, onLevel: (v, state) => {} });
//   hf.stop();
const HandsFree = {
  async start({ onSpeech, isBusy = () => false, onLevel = () => {}, silenceSec = 1.2 }) {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } });
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    await ctx.resume();
    const src = ctx.createMediaStreamSource(stream);
    // 音は AudioWorklet（音の処理専用の流れ）で受けて 2048 サンプルずつ渡す。
    // 古い ScriptProcessor だと、画面の処理が重い時に音が抜け落ち、話の途中で「黙った」と判断して切れてしまう
    const code = `class Tap extends AudioWorkletProcessor{constructor(){super();this.b=new Float32Array(2048);this.n=0;}
      process(i){const x=i[0]&&i[0][0];if(x){for(let k=0;k<x.length;k++){this.b[this.n++]=x[k];if(this.n===2048){this.port.postMessage(this.b);this.b=new Float32Array(2048);this.n=0;}}}return true;}}
      registerProcessor('hf-tap',Tap);`;
    await ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([code], { type: 'text/javascript' })));
    // 出力なしのノードにする（出力が無いノードは、どこにもつながなくても動き続ける）
    const proc = new AudioWorkletNode(ctx, 'hf-tap', { numberOfInputs: 1, numberOfOutputs: 0 });
    const sr = ctx.sampleRate, frameSec = 2048 / sr;
    let clock = 0;   // 受け取った音の量から数える時刻（画面の処理の遅れに左右されない）
    const PRE = Math.ceil(0.4 / frameSec);          // 話し始めの前 0.4 秒も一緒に送る
    const START = Math.ceil(0.12 / frameSec);       // 0.12 秒続いたら話し始め
    const END = Math.ceil(silenceSec / frameSec);   // これだけ黙ったら話し終わり
    const MIN = Math.ceil(0.35 / frameSec);         // 0.35 秒より短い音（咳・物音）は送らない
    const MAX = Math.ceil(15 / frameSec);           // 15 秒で区切る
    let floor = 0.01, pre = [], rec = null, loud = 0, quiet = 0, busyUntil = 0, sending = false;
    proc.port.onmessage = (ev) => {
      const x = ev.data; let s = 0;
      for (let i = 0; i < x.length; i++) s += x[i] * x[i];
      const rms = Math.sqrt(s / x.length), now = (clock += frameSec);
      const busy = sending || isBusy();
      if (busy) busyUntil = now + 0.4;              // キャラの声が止まってからも少しだけ待つ（残響を拾わない）
      if (busy || now < busyUntil) { rec = null; pre = []; loud = quiet = 0; onLevel(rms, 'wait'); return; }
      // 周りの音の大きさ（静かな時だけゆっくり追う）。話し声はその3倍以上
      if (!rec) floor = rms < floor ? floor * 0.9 + rms * 0.1 : floor * 0.998 + rms * 0.002;
      const on = Math.max(0.012, floor * 3), off = Math.max(0.008, floor * 2);
      const buf = x;
      if (!rec) {
        pre.push(buf); if (pre.length > PRE) pre.shift();
        loud = rms > on ? loud + 1 : 0;
        if (loud >= START) { rec = pre.slice(); pre = []; quiet = 0; }
        onLevel(rms, rec ? 'rec' : 'listen'); return;
      }
      rec.push(buf);
      quiet = rms < off ? quiet + 1 : 0;
      onLevel(rms, 'rec');
      if (quiet >= END || rec.length >= MAX) {
        const frames = rec, voiced = frames.length - quiet - PRE; rec = null; quiet = loud = 0;
        if (voiced < MIN) return;
        sending = true;
        Promise.resolve(onSpeech(toWav(frames, sr))).finally(() => { sending = false; });
      }
    };
    src.connect(proc);
    return { stop() { proc.disconnect(); src.disconnect(); stream.getTracks().forEach(t => t.stop()); ctx.close(); } };
  },
};

function toWav(frames, sr) {
  const n = frames.reduce((a, f) => a + f.length, 0), buf = new ArrayBuffer(44 + n * 2), v = new DataView(buf);
  const w = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  w(0, 'RIFF'); v.setUint32(4, 36 + n * 2, true); w(8, 'WAVE'); w(12, 'fmt '); v.setUint32(16, 16, true);
  v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, sr, true); v.setUint32(28, sr * 2, true);
  v.setUint16(32, 2, true); v.setUint16(34, 16, true); w(36, 'data'); v.setUint32(40, n * 2, true);
  let o = 44;
  for (const f of frames) for (let i = 0; i < f.length; i++, o += 2) v.setInt16(o, Math.max(-1, Math.min(1, f[i])) * 0x7fff, true);
  return new Blob([buf], { type: 'audio/wav' });
}
window.HandsFree = HandsFree;
