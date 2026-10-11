# AIキャラと声でおしゃべり（ローカルGPU + Claude）

スマホやPCのブラウザで話しかけると、キャラが**自分の声で**返事をする会話アプリです。

- 話し終わってから **約1秒で相槌**（「えへへ」「そうなの？」など、内容に合わせて選ぶ）
- **3〜4秒で本返事**の声
- 声と書き起こしは**自分のPCのGPU**で動く（[speech.cpp](https://github.com/nyosegawa/speech.cpp)）。外に音声を送らない
- 返事を考えるのは Claude。**Claude のサブスク（Pro/Max）でそのまま動く**。API キーでも可
- キャラは `characters/<名前>/character.json` を1つ書くだけで増やせる
- **ハンズフリー**：画面の「ハンズフリー」にチェックを入れると、ボタンを押さずに話しかけるだけで会話できる（話し始め・話し終わりを自動で見つける。キャラが話している間は聞かない）

> **キャラの絵（立ち絵1枚から作る2Dモデル）が口パクしながら話す版**は、別のリポジトリにあります → [local-2d-character-voice-chat](https://github.com/mosumosu12/local-2d-character-voice-chat)

### デモ（音声あり・約1分40秒）

https://github.com/mosumosu12/local-voice-character-chat/raw/main/docs/demo.mp4

男性の声で話しかけて、キャラ（ここあ）が声で返事をしています。話しかける側の声もこの仕組みのTTSで作ったもので、
**ハンズフリー**（ボタンを押さずに話しかけるだけ）で、実際に「書き起こし → 相槌 → 返事 → 声」を通しています。※デモのキャラの声・設定はこのリポジトリには含まれません。

```
ブラウザ（押して話す／ハンズフリー）
  └→ server.py ── 書き起こし（Qwen3-ASR）──┐
                 ├ 相槌を選ぶ（Jev / Claude）→ 先に一声だけ返す
                 └ 返事（Claude）→ 声（Irodori-TTS）→ ブラウザで再生
```

## 必要なもの

- Python 3.10 以上・ffmpeg
- speech.cpp を動かすPC（Windows/Linux は Vulkan、Mac は Metal。VRAM 8GB 程度〜）
- Claude Code（`claude` コマンド）＋ Claude の Pro/Max プラン、または Anthropic の API キー
- （任意）OpenRouter の API キー：相槌選びが速くなる（1回 約0.003円）

## 使い方

### 1. 声と書き起こしのサーバー（speech.cpp）を立てる

[speech.cpp のリリース](https://github.com/nyosegawa/speech.cpp/releases) から自分のPC用を落として、キャラの**参照声**（10秒前後の wav）を用意して起動します。

```
speech serve irodori-tts-mf qwen3-asr-1.7b --host 0.0.0.0 --port 8955 --add-voice hiyori=./hiyori_ref.wav
```

`--add-voice` の名前（ここでは `hiyori`）を `character.json` の `"voice"` に書きます。
（`--port` を付けないと speech.cpp は 8080 で待ち受けます。このアプリの既定は 8955 なので、上のように指定してください）

モデル名は `curl http://<speech.cppのPC>:8955/v1/models` で出てくる名前と合わせます。違っていたら環境変数 `ASR_MODEL` / `TTS_MODEL` で直します。

> ⚠ **参照声は、自分の声か、使ってよいと許可を得た声だけ**にしてください。
> Irodori-TTS のモデルカードでは、本人の同意なく実在の人（声優・有名人など）の声をまねることは禁止されています。

### 2. 返事役（Claude）のログイン

サブスクで動かす場合（自分用に使う時）: **`CLAUDE_CODE_OAUTH_TOKEN` が必須**です。
普段の Claude Code のログインは使いません（普段の設定や CLAUDE.md が会話に混ざらないよう、空の設定で起動するため）。

```
claude setup-token          # Claude Code の機能。表示されたトークンを↓に入れる
export CLAUDE_CODE_OAUTH_TOKEN=...
```

> サブスクのトークンを自分用のアプリで使ってよいかは、Anthropic の利用規約を各自で確認してください。人に使わせる時は API キーを使うのが安全です。

API で動かす場合（従量課金）: `export ANTHROPIC_API_KEY=...`

### 3. 相槌の声を作る

speech.cpp を別のPCで動かしている時は、先に `SPEECH_URL` を設定します（同じPCなら不要）。この設定は手順4でも使います。

```
export SPEECH_URL=http://<speech.cppのPC>:8955
CHARACTER=sample python3 make_fillers.py
```

### 4. 起動

```
CHARACTER=sample python3 server.py            # → http://localhost:8960/
```

既定ではこのPCからしか開けません。スマホから話す時は `--host 0.0.0.0` を付け、ブラウザの決まりで **https も必要**なので、自己署名の証明書を作って `--https` を付けます（初回だけ警告が出ます）。

```
openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 365 -subj /CN=voicechat
CHARACTER=sample python3 server.py 8960 --https --host 0.0.0.0
```

> ⚠ このサーバーにはパスワード等の認証がありません。**自宅のLANの中だけ**で使い、インターネットに公開しないでください（開けた人が誰でもあなたの Claude と GPU を使えてしまいます）。

## 設定（環境変数）

| 変数 | 既定 | 内容 |
|---|---|---|
| `SPEECH_URL` | `http://127.0.0.1:8955` | speech.cpp のサーバー |
| `CHARACTER` | `sample` | `characters/` の下のフォルダ名 |
| `CHARACTERS_DIR` | `./characters` | キャラのフォルダを別の場所に置く時 |
| `CLI_MODEL` | `sonnet` | サブスクで動かす時のモデル（`haiku` にすると速く軽い） |
| `API_MODEL` | `claude-sonnet-5-5` | API で動かす時のモデル |
| `OPENROUTER_API_KEY` | なし | あれば相槌を Jev で選ぶ（0.2〜0.5秒）。無ければ Claude Haiku（約0.9秒） |
| `ASR_MODEL` / `TTS_MODEL` | `Qwen3-ASR-1.7B` / `Irodori-TTS-v4.1-Small-MF` | speech.cpp のモデル名 |

## 自分のキャラを作る

`characters/sample/` をコピーして、`character.json` の `name` `system_prompt` `voice` `fillers` を書き換えるだけです。
相槌（`fillers`）は「いつ使うか（`when`）」を具体的に書くほど、外れが減ります。相槌のキーは**半角の英小文字だけ**にしてください（例 `ehehe`）。

## 作ってみて分かったコツ

- 返事の文は「。」で区切らず「、」でつないで最後だけ「。」にして TTS に渡す（1文ずつ作られて間が空くのを防ぐ）
- Irodori-TTS v4.1 は少しゆっくり話すので `speed` は 1.1 前後
- 相槌は「相槌なし」も選択肢に入れ、自信が低い時は出さない。外れた相槌は無いより悪い
- 本返事には「もう『えへへ』と言った」と伝えて、同じ言葉で始めないようにする
- 書き起こしはキャラ名をよく聞き間違えるので、返事役に「聞き間違いは意味を汲んで」と書いておく

## 使っているもの

- [speech.cpp](https://github.com/nyosegawa/speech.cpp)（MIT）
- [Irodori-TTS](https://huggingface.co/Aratako/Irodori-TTS-v4.1-Small-MF)（MIT＋利用上の注意：本人の同意なく実在人物の声をまねない・誤情報目的のディープフェイクに使わない）
- [Qwen3-ASR](https://huggingface.co/Qwen/Qwen3-ASR-1.7B)（Apache-2.0）
- [Jev](https://openrouter.ai/docs/guides/community/jev)（OpenRouter 経由・任意）
- Claude（Anthropic）

モデルの重みはこのリポジトリに含まれません。各配布元から取得してください。

## ライセンス

MIT
