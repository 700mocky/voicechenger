#!/usr/bin/env python3
"""
Discord ボイスチェンジャーボット

コマンド:
  !join                — ボイスチャンネルに参加して音声変換を開始
  !leave               — ボイスチャンネルから退出
  !pitch_up  / !up     — 高い声に変換 (+6 半音)
  !pitch_down / !down  — 低い声に変換 (-6 半音)
  !gender [male|female]— 異性の声に変換
  !normal    / !off    — 変換なし（スルー）
  !status    / !s      — 現在の設定を表示
"""

import os
import threading

from dotenv import load_dotenv

import discord
import discord.sinks
from discord.ext import commands

from voice_changer import VoiceChanger, DISCORD_FRAME_BYTES

load_dotenv()

# =============================================================================
# カスタム AudioSource — スレッドセーフなリングバッファ
# =============================================================================

class BufferedAudioSource(discord.AudioSource):
    """
    変換済み PCM をバッファリングして 20 ms フレーム単位に供給する AudioSource。
    バッファが空のときは無音（ゼロバイト）フレームを返す。
    discord.py の AudioPlayer スレッドから read() が呼ばれる。
    """

    def __init__(self) -> None:
        self._buf  = bytearray()
        self._lock = threading.Lock()

    # ---- AudioSource インターフェース ----

    def read(self) -> bytes:
        """20 ms 分の PCM (3840 bytes) を返す。バッファ不足時は無音。"""
        with self._lock:
            if len(self._buf) >= DISCORD_FRAME_BYTES:
                frame = bytes(self._buf[:DISCORD_FRAME_BYTES])
                del self._buf[:DISCORD_FRAME_BYTES]
                return frame
        return b"\x00" * DISCORD_FRAME_BYTES   # 無音フレーム

    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        with self._lock:
            self._buf.clear()

    # ---- 書き込み (受信スレッドから呼ばれる) ----

    def push(self, data: bytes) -> None:
        """変換済み音声データをバッファに追加する。"""
        with self._lock:
            self._buf.extend(data)

    @property
    def buffered_ms(self) -> float:
        """現在バッファリングされている音声の長さ (ms)。"""
        with self._lock:
            bytes_per_ms = DISCORD_FRAME_BYTES / 20
            return len(self._buf) / bytes_per_ms


# =============================================================================
# カスタム Sink — ユーザー音声受信 → ピッチシフト → バッファ書き込み
# =============================================================================

class VoiceChangerSink(discord.sinks.Sink):
    """
    discord.py の録音 Sink。
    ユーザーごとに PCM を受信し、VoiceChanger でピッチシフトして
    BufferedAudioSource に積む。一定量溜まったら VoiceClient で再生開始。
    """

    # 1 回の処理単位: 10 フレーム × 20 ms = 200 ms
    # → 短すぎると librosa の品質が落ちるため 200 ms が最低ライン
    PROCESS_FRAMES = 10

    def __init__(self, changer: VoiceChanger, vc: discord.VoiceClient) -> None:
        super().__init__()
        self.changer = changer
        self.vc      = vc

        self.audio_source = BufferedAudioSource()
        self._user_bufs: dict[int, bytearray] = {}
        self._chunk_bytes = DISCORD_FRAME_BYTES * self.PROCESS_FRAMES
        self._play_lock   = threading.Lock()

    # ---- discord.sinks.Sink インターフェース ----

    def write(self, data: bytes, user: int) -> None:
        """各ユーザーの 20 ms PCM フレームを受信する（受信スレッドから呼ばれる）。"""
        buf = self._user_bufs.setdefault(user, bytearray())
        buf.extend(data)

        # 200 ms 溜まったらピッチシフト → バッファに積む
        while len(buf) >= self._chunk_bytes:
            chunk = bytes(buf[: self._chunk_bytes])
            del buf[: self._chunk_bytes]
            processed = self.changer.process(chunk)
            self.audio_source.push(processed)

        # 初回データが来たら再生開始（1 度だけ）
        with self._play_lock:
            if not self.vc.is_playing():
                try:
                    self.vc.play(
                        self.audio_source,
                        after=lambda err: print(f"[Playback error] {err}") if err else None,
                    )
                except Exception as exc:
                    print(f"[VoiceChangerSink] play() failed: {exc}")

    def cleanup(self) -> None:
        self._user_bufs.clear()
        self.audio_source.cleanup()
        if self.vc.is_playing():
            self.vc.stop()


# =============================================================================
# Bot 設定
# =============================================================================

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states    = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    description="リアルタイムボイスチェンジャーボット",
)

# ギルドごとの状態管理
_changers: dict[int, VoiceChanger]       = {}
_sinks:    dict[int, VoiceChangerSink]   = {}


def get_changer(guild_id: int) -> VoiceChanger:
    if guild_id not in _changers:
        _changers[guild_id] = VoiceChanger()
    return _changers[guild_id]


# =============================================================================
# イベント
# =============================================================================

@bot.event
async def on_ready() -> None:
    assert bot.user is not None
    print(f"[Bot] ログイン成功: {bot.user}  (ID: {bot.user.id})")
    print("[Bot] コマンド: !join !leave !pitch_up !pitch_down !gender !normal !status")
    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.listening, name="!join")
    )


@bot.event
async def on_command_error(ctx: commands.Context, error: Exception) -> None:
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ 引数が不足しています: `{error.param.name}`")
        return
    print(f"[Error] {error}")
    await ctx.send(f"❌ エラーが発生しました: {error}")


@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after:  discord.VoiceState,
) -> None:
    """ボットだけが残ったチャンネルから自動退出する。"""
    if member.bot:
        return
    vc = member.guild.voice_client
    if vc and vc.channel and len(vc.channel.members) <= 1:
        await vc.disconnect()
        _sinks.pop(member.guild.id, None)
        print(f"[Bot] 全員退出のため {vc.channel.name} から退出しました")


# =============================================================================
# ユーティリティ
# =============================================================================

async def _ensure_voice(ctx: commands.Context) -> discord.VoiceClient | None:
    """コマンド投稿者がいるボイスチャンネルにボットを接続する。"""
    if not ctx.author.voice:                   # type: ignore[union-attr]
        await ctx.send("❌ まずボイスチャンネルに参加してください。")
        return None
    channel = ctx.author.voice.channel         # type: ignore[union-attr]
    if ctx.voice_client:
        if ctx.voice_client.channel != channel:
            await ctx.voice_client.move_to(channel)
        return ctx.voice_client                # type: ignore[return-value]
    return await channel.connect()


def _gid(ctx: commands.Context) -> int:
    return ctx.guild.id                        # type: ignore[union-attr]


# =============================================================================
# コマンド
# =============================================================================

@bot.command(name="join", aliases=["j"], help="ボイスチャンネルに参加して音声変換を開始します")
async def cmd_join(ctx: commands.Context) -> None:
    vc = await _ensure_voice(ctx)
    if vc is None:
        return

    gid     = _gid(ctx)
    changer = get_changer(gid)

    # 既存録音をリセット
    if vc.is_recording():
        vc.stop_recording()
    if vc.is_playing():
        vc.stop()

    sink = VoiceChangerSink(changer, vc)
    _sinks[gid] = sink

    async def _after(s: discord.sinks.Sink, ch: discord.TextChannel) -> None:
        pass   # 録音終了コールバック（今回は使用しない）

    vc.start_recording(sink, _after, ctx.channel)

    await ctx.send(
        f"✅ **{vc.channel.name}** に参加しました！\n"
        f"現在のモード: **{changer.description}**\n"
        f"ボットがあなたの声を変換してチャンネルに再生します。\n"
        f"> ⚠️ 自分の声が二重に聞こえる場合は Discord でマイクをサーバーミュートしてください。"
    )


@bot.command(name="leave", aliases=["l"], help="ボイスチャンネルから退出します")
async def cmd_leave(ctx: commands.Context) -> None:
    if not ctx.voice_client:
        await ctx.send("❌ ボットはボイスチャンネルに参加していません。")
        return

    gid = _gid(ctx)
    if ctx.voice_client.is_recording():
        ctx.voice_client.stop_recording()
    await ctx.voice_client.disconnect()
    _sinks.pop(gid, None)
    await ctx.send("👋 ボイスチャンネルから退出しました。")


@bot.command(name="pitch_up", aliases=["up"], help="声を高くします (+6 半音)")
async def cmd_pitch_up(ctx: commands.Context) -> None:
    changer = get_changer(_gid(ctx))
    changer.set_high()
    await ctx.send(f"🔼 モード変更: **{changer.description}**")


@bot.command(name="pitch_down", aliases=["down"], help="声を低くします (-6 半音)")
async def cmd_pitch_down(ctx: commands.Context) -> None:
    changer = get_changer(_gid(ctx))
    changer.set_low()
    await ctx.send(f"🔽 モード変更: **{changer.description}**")


@bot.command(
    name="gender",
    aliases=["g"],
    help="異性の声に変換します。引数: male（男→女）または female（女→男）",
)
async def cmd_gender(ctx: commands.Context, base: str = "male") -> None:
    changer        = get_changer(_gid(ctx))
    male_to_female = base.lower() not in ("female", "f", "女", "2")
    changer.set_gender(male_to_female)
    await ctx.send(f"⚧ モード変更: **{changer.description}**")


@bot.command(name="normal", aliases=["n", "off"], help="音声変換を無効にします")
async def cmd_normal(ctx: commands.Context) -> None:
    changer = get_changer(_gid(ctx))
    changer.set_normal()
    await ctx.send(f"➡️ モード変更: **{changer.description}**")


@bot.command(name="status", aliases=["s", "info"], help="現在の設定を表示します")
async def cmd_status(ctx: commands.Context) -> None:
    changer = get_changer(_gid(ctx))
    vc      = ctx.voice_client

    embed = discord.Embed(
        title="🎙️ ボイスチェンジャー ステータス",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="接続状態",
        value=f"✅ **{vc.channel.name}**" if vc else "❌ 未接続 (`!join` で開始)",
        inline=False,
    )
    embed.add_field(name="現在のモード",  value=f"**{changer.description}**",    inline=True)
    embed.add_field(name="半音シフト量",  value=f"`{changer.semitones:+.0f}` 半音", inline=True)
    embed.add_field(
        name="コマンド一覧",
        value=(
            "`!join`  / `!j`          — ボイスチャンネルに参加\n"
            "`!leave` / `!l`          — ボイスチャンネルから退出\n"
            "`!pitch_up` / `!up`      — 高い声 (+6 半音)\n"
            "`!pitch_down` / `!down`  — 低い声 (-6 半音)\n"
            "`!gender [male/female]`  — 異性の声\n"
            "`!normal` / `!off`       — 変換なし\n"
            "`!status` / `!s`         — このステータス表示"
        ),
        inline=False,
    )
    await ctx.send(embed=embed)


# =============================================================================
# エントリーポイント
# =============================================================================

def main() -> None:
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "DISCORD_BOT_TOKEN が設定されていません。\n"
            "  1. cp .env.example .env\n"
            "  2. .env を編集してトークンを貼り付けてください。"
        )
    print("[Bot] 起動中...")
    bot.run(token)


if __name__ == "__main__":
    main()
