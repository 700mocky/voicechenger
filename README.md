# Discord ボイスチェンジャー for Mac

リアルタイム音声変換ツール。2 つの使い方があります。

| 方式 | スクリプト | 説明 |
|------|-----------|------|
| **ローカル変換** | `main.py` | BlackHole 経由で Discord に流す |
| **ボットで変換** | `bot.py`  | Discord ボットがボイスチャンネル内で変換・再生 |

---

## 方式 1: ローカル変換 (main.py + BlackHole)

マイク音声を Python で変換し、BlackHole 仮想オーディオへ出力。
Discord はその BlackHole を「マイク」として拾います。

### 手順

#### 1. BlackHole のインストール

```bash
brew install blackhole-2ch
```

インストール後に **Mac を再起動** してください。

> 手動インストールは https://github.com/ExistentialAudio/BlackHole から PKG をダウンロード。

#### 2. Python ライブラリのインストール

```bash
pip install -r requirements.txt
```

#### 3. アクセシビリティ権限（キーボード制御に必要）

**システム設定 → プライバシーとセキュリティ → アクセシビリティ** でターミナルを追加。

#### 4. スクリプト起動

```bash
python main.py
```

起動後にマイクのデバイス番号を選択し、BlackHole が自動検出されます。

#### 5. Discord の設定

**設定（歯車）→ 音声・ビデオ → 入力デバイス → BlackHole 2ch** を選択。

### 操作キー

| キー | モード |
|------|--------|
| `1`  | ノーマル（変換なし） |
| `2`  | 高い声 (+6 半音) |
| `3`  | 低い声 (-6 半音) |
| `4`  | 異性の声 (±10 半音) |
| `q`  | 終了 |

---

## 方式 2: Discord ボット (bot.py)

ボットがボイスチャンネルに入り、全員の声をリアルタイムで変換して再生します。

### 仕組み

```
ユーザー発話
  └─→ Discord サーバー
        └─→ ボット受信 (discord.sinks)
              └─→ ピッチシフト処理 (librosa / scipy)
                    └─→ ボットが変換音声をチャンネルに再生
```

> **注意**: ボットが再生する変換音声と元の声が二重に聞こえる場合は、
> Discord でご自身をサーバーミュートしてください。

---

### ボットのセットアップ手順

#### 1. Discord Developer Portal でボットを作成

1. https://discord.com/developers/applications へアクセス
2. **New Application** → アプリ名を入力（例: `VoiceChangerBot`）
3. 左メニュー **Bot** → **Add Bot** → **Yes, do it!**
4. **Token** の **Reset Token** → コピーしておく（後で使用）

#### 2. 必要な Privileged Intents を有効化

Bot ページの **Privileged Gateway Intents** で以下を ON にする：

- **Message Content Intent** ✅（コマンド読み取りに必要）

#### 3. ボットをサーバーに招待

1. 左メニュー **OAuth2 → URL Generator**
2. **Scopes** で `bot` を選択
3. **Bot Permissions** で以下を選択:
   - `Read Messages / View Channels`
   - `Send Messages`
   - `Connect`（ボイスチャンネル接続）
   - `Speak`（ボイスチャンネルで発話）
   - `Use Voice Activity`
4. 生成された URL をブラウザで開き、サーバーに招待

#### 4. 環境変数の設定

```bash
# .env.example をコピー
cp .env.example .env

# .env を編集してトークンを設定
```

`.env` の内容:

```
DISCORD_BOT_TOKEN=your_bot_token_here   ← 手順 1-4 で取得したトークン
```

> ⚠️ `.env` ファイルは **絶対に GitHub にコミットしないでください**（`.gitignore` で除外済み）。

#### 5. 依存ライブラリのインストール

```bash
pip install -r requirements.txt
```

Opus ライブラリが必要な場合:

```bash
brew install opus ffmpeg
```

#### 6. ボット起動

```bash
python bot.py
```

```
[VoiceChanger] ピッチシフトエンジン: librosa
[Bot] 起動中...
[Bot] ログイン成功: VoiceChangerBot#1234  (ID: 123456789)
[Bot] コマンド: !join !leave !pitch_up !pitch_down !gender !normal !status
```

---

### ボットのコマンド

| コマンド | エイリアス | 説明 |
|---------|-----------|------|
| `!join` | `!j` | ボイスチャンネルに参加して変換開始 |
| `!leave` | `!l` | ボイスチャンネルから退出 |
| `!pitch_up` | `!up` | 高い声 (+6 半音) |
| `!pitch_down` | `!down` | 低い声 (-6 半音) |
| `!gender [male\|female]` | `!g` | 異性の声（引数で男→女 / 女→男） |
| `!normal` | `!off`, `!n` | 変換なし |
| `!status` | `!s`, `!info` | 現在の設定を表示 |

#### 使用例

```
!join               → ボットがあなたのいるチャンネルに参加
!pitch_up           → 高い声モードに変更
!gender male        → 男→女変換モード
!gender female      → 女→男変換モード
!status             → 現在の設定を埋め込みで表示
!leave              → ボットが退出
```

---

## ピッチシフトエンジン

品質の高い順に自動選択されます:

| 優先度 | エンジン | インストール方法 |
|--------|---------|----------------|
| 1 (最高) | pyrubberband | `brew install rubberband && pip install pyrubberband` |
| 2 (高)   | librosa      | `pip install librosa`（requirements.txt に含まれる） |
| 3 (標準) | scipy        | フォールバック（追加インストール不要） |

---

## ファイル構成

```
voicechenger/
├── README.md           ← このファイル
├── requirements.txt    ← 依存ライブラリ
├── .env.example        ← 環境変数テンプレート
├── .gitignore
│
├── main.py             ← ローカル変換 (BlackHole 方式)
├── bot.py              ← Discord ボット本体
└── voice_changer.py    ← ボット用音声変換モジュール
```

---

## トラブルシューティング

### `No module named 'discord'`

```bash
pip install "discord.py[voice]"
```

### `PortAudioError` (main.py)

デバイス番号が間違っています。起動時に表示される一覧で確認してください。

### ボットが声を再生しない / 無音

1. ボットの **Speak** 権限を確認してください
2. `!leave` → `!join` で再接続してみてください
3. ボイスチャンネルに他のユーザーが話しているか確認してください（無音時は処理しません）

### 声が遅延する

`bot.py` または `main.py` の `BLOCK_SIZE` / `PROCESS_FRAMES` を下げてください。
その分、ピッチシフトの品質は下がります。

```python
# bot.py
PROCESS_FRAMES = 5   # 100 ms（デフォルト 10 = 200 ms）

# main.py
BLOCK_SIZE = 2048    # 46 ms（デフォルト 4096 = 93 ms）
```

### Intents エラー (`PrivilegedIntentsRequired`)

Discord Developer Portal の Bot ページで **Message Content Intent** を有効にしてください。

---

## ライセンス

MIT
