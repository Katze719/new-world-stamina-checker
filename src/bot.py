import discord
from discord.ext import tasks 
from discord import app_commands
import yt_dlp
import os
import re
import asyncio
import shutil
import time
import json
import numpy as np
import colorCheck
import spreadsheet.authenticate
import spreadsheet.memberlist
import spreadsheet.payoutlist
import spreadsheet.stats
import spreadsheet.urlaub
from videoAnalyzer import VideoAnalyzer
from collections import deque
from google import genai
from logger import log
import matplotlib
import textExtract
import jsonFileManager
from typing import Optional
import datetime
from zoneinfo import ZoneInfo  # Erfordert Python 3.9+
import sqlite3
import spreadsheet
import matplotlib.pyplot as plt
import io
import matplotlib.dates as mdates
import matplotlib.font_manager as fm

matplotlib.use('Agg')  # Nutzt ein nicht-interaktives Backend für Speicherung


DISCORD_TOKEN = os.getenv("BOT_TOKEN")
GOOGLE_GEMINI_TOKEN = os.getenv("GOOGLE_GEMINI_TOKEN")

DOWNLOAD_FOLDER = "./downloads/"
OUTPUT_FOLDER = "./output/"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

spreadsheet_acc = spreadsheet.authenticate.create_gspread_manager()

stamina_lock = asyncio.Lock()
stamina_queue = deque()

def ensure_hidden_attribute(data):
    for channel_id, info in data.items():
        if "hidden" not in info:
            info["hidden"] = False
    return data

# Datei, in der die Kanäle gespeichert werden
VOD_CHANNELS_FILE_PATH = "./vod_channels.json"
ROLE_NAME_UPDATE_SETTINGS_PATH = "./role_name_settings.json"
GP_CHANNEL_IDS_FILE = "./gp_channel_ids.json"
SPREADSHEET_ROLE_SETTINGS_PATH = "./spreadsheet_role_settings.json"
WRITTEN_RAIDHELPERS_FILE = "./written_raidhelpers.json"

vod_channel_manager = jsonFileManager.JsonFileManager(VOD_CHANNELS_FILE_PATH, ensure_hidden_attribute)
settings_manager = jsonFileManager.JsonFileManager(ROLE_NAME_UPDATE_SETTINGS_PATH)
gp_channel_manager = jsonFileManager.JsonFileManager(GP_CHANNEL_IDS_FILE)
spreadsheet_role_settings_manager = jsonFileManager.JsonFileManager(SPREADSHEET_ROLE_SETTINGS_PATH)
written_raidhelpers_manager = jsonFileManager.JsonFileManager(WRITTEN_RAIDHELPERS_FILE)

# Initialize SQLite database for level system
DB_PATH = "./level_system.db"

# Level system constants
MAX_LEVEL = 100

def get_xp_requirement(level):
    """Berechnet die XP-Anforderung für ein bestimmtes Level mit einer mathematischen Formel.
    Verwendet eine exponentiell steigende Formel: 100 * (1.1^(level-1))
    Level 1: 100 XP
    Level 10: ~236 XP
    Level 50: ~10,000 XP
    Level 100: ~138,000 XP"""
    if level <= 0:
        return 0
    
    # Basiswert für Level 1
    base_xp = 100
    
    # Wachstumsrate pro Level
    growth_rate = 1.1
    
    # Exponentielles Wachstum
    return int(base_xp * (growth_rate ** (level - 1)))

def get_xp_for_level(level):
    """Calculate total XP needed for a specific level"""
    if level <= 0:
        return 0
    
    # Cap at max level
    if level > MAX_LEVEL:
        level = MAX_LEVEL
    
    # Sum all XP requirements up to this level
    total_xp = 0
    for i in range(1, level + 1):
        total_xp += get_xp_requirement(i)
    return total_xp

def init_level_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Create users table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS user_levels (
        user_id TEXT PRIMARY KEY,
        username TEXT,
        xp INTEGER DEFAULT 0,
        level INTEGER DEFAULT 0,
        message_count INTEGER DEFAULT 0,
        voice_time INTEGER DEFAULT 0,
        streak_days INTEGER DEFAULT 0,
        streak_multiplier REAL DEFAULT 1.0,
        last_active TEXT
    )
    ''')
    
    # Create voice activity tracking table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS voice_sessions (
        user_id TEXT,
        channel_id TEXT,
        start_time INTEGER,
        is_active BOOLEAN DEFAULT 1,
        PRIMARY KEY (user_id, channel_id, start_time)
    )
    ''')
    
    # Create XP history tracking table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS xp_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        amount INTEGER,
        reason TEXT,
        timestamp TEXT,
        FOREIGN KEY (user_id) REFERENCES user_levels(user_id)
    )
    ''')
    
    # Add streak columns if they don't exist
    try:
        cursor.execute("ALTER TABLE user_levels ADD COLUMN streak_days INTEGER DEFAULT 0")
        cursor.execute("ALTER TABLE user_levels ADD COLUMN streak_multiplier REAL DEFAULT 1.0")
        cursor.execute("ALTER TABLE user_levels ADD COLUMN last_active TEXT")
        log.info("Added streak columns to user_levels table")
    except sqlite3.OperationalError:
        # Columns already exist
        pass
    
    conn.commit()
    conn.close()

# Initialize database
init_level_db()

# Store active voice users {user_id: {channel_id: start_time}}
active_voice_users = {}
# Track user activity in voice channels {user_id: {channel_id: {"start_time": timestamp, "last_spoke": timestamp, "is_muted": bool}}}
voice_activity_tracker = {}

role_name_update_settings_cache = {}

default_pattern = "{name} ({level}) [{icons}]"
if "global_pattern" not in role_name_update_settings_cache:
    role_name_update_settings_cache["global_pattern"] = default_pattern

async def edit_msg(interaction: discord.Interaction, msg_id: int, embed: discord.Embed):
    try:
        msg = await interaction.channel.fetch_message(msg_id)
        if msg:
            await msg.edit(embed=embed)
    except (discord.NotFound, discord.HTTPException) as e:
        log.error(f"Message wurde wahrscheinlich gelöscht: {str(e)}")

async def download_video(youtube_url):
    video_path = f"{DOWNLOAD_FOLDER}video.mp4"
    
    ydl_opts = {
        "outtmpl": video_path,
        'format': 'bestvideo[height<=1080]+bestaudio/best',
        'merge_output_format': 'mp4',
    }
    
    await asyncio.to_thread(lambda: yt_dlp.YoutubeDL(ydl_opts).download([youtube_url]))
    return video_path

async def send_images(interaction: discord.Interaction, folder_path: str):
    """Sendet alle Bilder aus einem Ordner in 10er-Blöcken als ephemere Nachrichten."""
    files = [f for f in os.listdir(folder_path) if f.endswith(('.png', '.jpg', '.jpeg', '.gif'))]

    if not files:
        await interaction.channel.send("Keine Bilder im Ordner gefunden.")
        return

    # In 10er-Gruppen aufteilen
    batch_size = 10
    for i in range(0, len(files), batch_size):
        batch_files = files[i:i + batch_size]  # 10 Bilder pro Durchlauf

        file_objects = []
        for file in batch_files:
            file_path = os.path.join(folder_path, file)
            if os.path.exists(file_path):  # Überprüfen, ob die Datei existiert
                file_objects.append(discord.File(file_path, filename=file))

        if file_objects:
            await interaction.channel.send(files=file_objects)

def format_time(seconds):
    """Wandelt Sekunden in ein MM:SS Format um."""
    if seconds == 0:
        return "00:00"
    minutes = int(seconds // 60)
    seconds = int(seconds % 60)
    return f"{minutes:02}:{seconds:02}"

@tree.command(name="add_this_channel", description="Füge diesen Channel zur VOD-Prüfliste hinzu")
async def add_this_channel(interaction: discord.Interaction, hidden: bool = False):
    channels = await vod_channel_manager.load()
    channel_id = str(interaction.channel.id)

    if channel_id in channels:
        await interaction.response.send_message("Dieser Channel ist bereits in der VOD-Prüfliste.", ephemeral=True)
        return

    channels[channel_id] = {"hidden": hidden}
    await vod_channel_manager.save(channels)
    await interaction.response.send_message(
        f"Channel wurde erfolgreich zur VOD-Prüfliste hinzugefügt! (Hidden: {hidden})",
        ephemeral=True
    )

@tree.command(name="remove_this_channel", description="Entferne diesen Channel von der VOD-Prüfliste")
async def remove_this_channel(interaction: discord.Interaction):
    channels = await vod_channel_manager.load()
    channel_id = str(interaction.channel.id)

    if channel_id not in channels:
        await interaction.response.send_message("Dieser Channel ist nicht in der VOD-Prüfliste.", ephemeral=True)
        return

    del channels[channel_id]
    await vod_channel_manager.save(channels)
    await interaction.response.send_message("Channel wurde erfolgreich von der VOD-Prüfliste entfernt!", ephemeral=True)

@tree.command(name="stamina_check", description="Analysiert ein YouTube-Video auf Stamina-Null-Zustände.")
async def stamina_check(interaction: discord.Interaction, youtube_url: str, debug_mode: bool = False):

    e = discord.Embed(title="Deaktiviert")
    await interaction.response.send_message(embed=e)
    return

    async def send_image(path, filename):
        if os.path.exists(path):  # Überprüfen, ob die Datei existiert
            await interaction.channel.send(file=discord.File(path, filename=filename))


    stamina_queue.append(interaction.id)
    position = len(stamina_queue)

    log.info(f"Neue anfrage von {interaction.user.display_name}, warteschlange ist {len(stamina_queue)}")

    base_msg = discord.Embed(
        title="🏁 Stamina Check startet gleich!",
        description="🔍 Bereite alles vor...\n\n⚙️ **Warteschlange wird organisiert...**\n\n🕐 Bitte habe etwas Geduld!",
        color=discord.Color.blue()
    )

    embed = discord.Embed(
        title="⏳ Warteschlange",
        description=f"Du bist auf Platz {position} in der Warteschlange.",
        color=discord.Color.blue()
    )
    await interaction.response.send_message(embed=base_msg, ephemeral=True)
    
    msg = await interaction.followup.send(embed=embed, wait=True)

    while stamina_queue[0] != interaction.id:
        await asyncio.sleep(10)
        new_position = stamina_queue.index(interaction.id) + 1
        embed.description = f"Du bist jetzt auf Platz {new_position} in der Warteschlange."
        await edit_msg(interaction, msg.id, embed)

    embed.description = "Du bist als nächstes drann."
    await edit_msg(interaction, msg.id, embed)

    async with stamina_lock:
        stamina_queue.popleft()
        try:
            log.info(f"Geht los für {interaction.user.display_name}")

            shutil.rmtree(DOWNLOAD_FOLDER)
            shutil.rmtree(OUTPUT_FOLDER)
            os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
            os.makedirs(OUTPUT_FOLDER, exist_ok=True)

            embed.title = "📥 Video-Download"
            embed.description = "Lade Video herunter..."
            await edit_msg(interaction, msg.id, embed)

            log.info("Starte Download")
            time_start_download = time.time()
            video_path = await download_video(youtube_url)
            time_end_download = time.time()

            
            video_analyzer = VideoAnalyzer(video_path, debug=debug_mode)
            skip_first_frames = 100
            skip_first_frames = skip_first_frames if skip_first_frames > video_analyzer.frame_count else 0 
            training_frame_count = int(video_analyzer.frame_count * 0.8)

            embed.title = "🚀 Training läuft"
            embed.description = f"Trainiere Algorythmus mit {training_frame_count} von {video_analyzer.frame_count} Frames..."
            await edit_msg(interaction, msg.id, embed)

            log.info("Starte Training")
            time_start_training = time.time()
            stable_rectangle = await video_analyzer.find_stable_rectangle(training_frame_count, skip_first_frames)
            time_end_training = time.time()

            if debug_mode:
                embed.title = "Stabiles Rechteck"
                embed.description = f"Gefunden auf: {stable_rectangle}"
                await interaction.channel.send(embed=embed)

            embed.title = "🔍 Analyse läuft"
            embed.description = f"Analysiere {video_analyzer.frame_count} Frames..."
            await edit_msg(interaction, msg.id, embed)

            async def send_progress_update(processed: int, total: int):
                embed = discord.Embed(
                    title="🔍 Analyse läuft...",
                    description=f"Fortschritt: {processed} von {total} Frames analysiert.",
                    color=discord.Color.blue()
                )
                log.info(f"Fortschritt: {processed} von {total} Frames analysiert.")
                await edit_msg(interaction, msg.id, embed)

            log.info("Starte Analyse")
            time_start_analyze = time.time()
            timestamps = await video_analyzer.analyze_video(stable_rectangle, send_progress_update)
            time_end_analyze = time.time()

            message = await get_feedback_message(len(timestamps))

            embed.title = f"✅ Analyse abgeschlossen! für {youtube_url}"

            if debug_mode:
                t_info = f"**Verbrauche Zeit:** {format_time(time_end_analyze - time_start_download)}\n- Download: {format_time(time_end_download - time_start_download)}\n- Training: {format_time(time_end_training - time_start_training)}\n- Analyse: {format_time(time_end_analyze - time_start_analyze)}\n\n"
            else:
                t_info = ""
            embed.description = f"{t_info}⏱ **An Folgenden Stellen bist du Out Of Stamina:**\n"

            # Liste für die drei Gruppen
            fields = ["", "", ""]
            # Alle Timestamps durchgehen und in die passende Gruppe einordnen
            num_groups = max(1, len(timestamps) // 3)  # Verhindert Division durch 0

            for index, timestamp in enumerate(timestamps, start=1):
                group_index = (index - 1) // num_groups  # Bestimmt die Gruppe (0, 1 oder 2)
                group_index = min(group_index, 2)  # Falls `remaining_items` existiert, Begrenzung auf max. 2
                fields[group_index] += f"**#{index}.** {timestamp}\n"

            # Felder zum Embed hinzufügen
            for field_content in fields:
                embed.add_field(name="", value=field_content, inline=True)

            embed.add_field(name="", value=message)

            embed.color = discord.Color.green()
            await edit_msg(interaction, msg.id, embed)
        except Exception as e:
            embed.title = "❌ Fehler"
            embed.description = str(e)
            embed.color = discord.Color.red()
            await edit_msg(interaction, msg.id, embed)

        if debug_mode:
            await send_images(interaction, OUTPUT_FOLDER)
            await send_image(f"{video_analyzer.output_dir_debug}/debug.jpg", "debug.jpg")
            if not len(video_analyzer.yellow_hex_colors):
                log.error("fuck")
                return
            t = colorCheck.get_hsv_range_from_hex_list(list(video_analyzer.yellow_hex_colors))
            await interaction.channel.send(str(t))
            s = sorted(video_analyzer.yellow_hex_colors)
            await interaction.channel.send(f"First: {s[:50]}\n\nLast: {s[-50:]}")
            current_gradiant = colorCheck.generate_hsv_gradient((t[0][0], t[1][0]), (t[0][1], t[1][1]), (t[0][2], t[1][2]))
            lo = video_analyzer.lower_yellow
            up = video_analyzer.upper_yellow
            expected_gradiant = colorCheck.generate_hsv_gradient((lo[0], up[0]), (lo[1], up[1]), (lo[2], up[2]))
            fig, axs = plt.subplots(2, 1, figsize=(20, 10), dpi=200)
            axs[0].imshow(current_gradiant)
            axs[0].set_title(f"Detected Color Range: {t}")
            axs[0].axis("off")
            axs[1].imshow(expected_gradiant)
            axs[1].set_title(f"Expected Color Range: {lo}:{up}")
            axs[1].axis("off")
            plt.savefig("hsv_gradient.png")
            await send_image("./hsv_gradient.png", "gradient.png")

        log.info(f"Anfrage Fertig von {interaction.user.display_name}")

@tree.command(name="get_queue_length", description="Zeit die länge der Warteschlange an.")
async def get_queue_length(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Warteschlange",
        description=f"In der Warteschlange sind aktuell {len(stamina_queue)} VOD's.",
        color=discord.Color.blue()
    )
    await interaction.response.send_message(embed=embed)

async def get_feedback_message(stamina_events):
    client = genai.Client(api_key=GOOGLE_GEMINI_TOKEN)
    c = f"""
Du bist ein erfahrener Coach für das Spiel 'New World: Aeternum' und bewertest das Stamina-Management eines Spielers auf humorvolle und motivierende Weise. Weniger Out-of-Stamina-Momente sind besser. Die Bewertung wird strenger, je höher die Anzahl der Ereignisse ist, aber alles unter 10 ist top und verdient nur Lob. Halte dich an folgende Kategorien und formuliere deine Antwort spielerisch mit Memes, Insider-Witzen und passenden Emojis.

Anzahl < 10 → Absolut top, nichts zu bemängeln. Kurz loben und feiern! 🏆🔥
Anzahl < 20 → Ausbaufähig, aber solide. Leicht humorvolles Anstupsen zur Verbesserung. ⚡💪
Anzahl < 30 → Übungsbedarf. Deutlichere Kritik mit Witz, aber noch motivierend. 🛠️😅
Anzahl ≥ 40 → Bench. Strengere, aber immer noch humorvolle Kritik. Man sollte merken, dass es ernst wird. 🚑💀

Die Person war {stamina_events} Mal out of stamina im letzten Krieg. Gib nur einen einzigen kurzen Satz aus, spielerisch, mit Emojis, aber passend zur Zahl!
"""
    return (await client.aio.models.generate_content(model="gemini-2.0-flash", contents=c)).text


@bot.event
async def on_ready():
    global role_name_update_settings_cache
    role_name_update_settings_cache = await settings_manager.load()

    if not check_channel.is_running():
        check_channel.start()
        
    if not check_for_raidhelpers.is_running():
        check_for_raidhelpers.start()
        
    if not reward_voice_activity.is_running():
        reward_voice_activity.start()
    
    # Streak-Update-Task starten
    if not update_streaks.is_running():
        update_streaks.start()

    log.info(f"Bot ist eingeloggt als {bot.user}")
    try:
        synced = await tree.sync()
        log.info(f"{len(synced)} Slash Commands synchronisiert.")
    except Exception as e:
        log.info(e)

# YouTube-Link-Erkennung
YOUTUBE_REGEX = re.compile(r"(https?://)?(www\.)?(youtube\.com|youtu\.?be)/.+")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    
    # Add XP for sending a message
    leveled_up, new_level = await add_message_xp(message.author.id, message.author.display_name)
    
    # Update nickname if level up without notification
    if leveled_up:
        await update_member_nickname(message.author)
    
    # Check if user is in a voice channel and record activity
    if message.author.voice and message.author.voice.channel:
        await record_user_voice_activity(message.author.id, message.author.voice.channel.id)
    
    watch_user_exctaction_channel = (await gp_channel_manager.load()).get("watch_user_exctaction_channel", None)
    if watch_user_exctaction_channel and str(message.channel.id) == str(watch_user_exctaction_channel):
        users = await extractUsers(message)    
        if users:
            # convert to csv string
            users = ",".join(users)
            await message.channel.send(f"Users: {users}")

    channels = await vod_channel_manager.load()
    if str(message.channel.id) not in channels:
        return
    
    match = YOUTUBE_REGEX.search(message.content)
    if match:
        stamina_queue.append(message.id)

        youtube_url = match.group()

        embed = discord.Embed()
        embed.title = f"Neues VOD von {message.author.display_name}"
        embed.description = f"{message.content}\n{message.created_at}\n\nJump: {message.jump_url}"
        embed.color = discord.Color.blue()

        channel = bot.get_channel(1337499488272519299)
        if channel:
            await channel.send(embed=embed)
            pass

        return

        channel_hidden = channels[str(message.channel.id)]["hidden"]
        coach_channel = bot.get_channel(1338135324500562022)
    
        if channel_hidden == False:
            await message.add_reaction("⏳")

        attempt = 0
        retries = 60
        while attempt < retries:
            try:
                while stamina_queue[0] != message.id:
                    await asyncio.sleep(10)

                async with stamina_lock:
                    attempt += 1
                    stamina_queue.popleft()
                    
                    shutil.rmtree(DOWNLOAD_FOLDER)
                    os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

                    log.info(f"Bearbeite VOD hidden for {message.author.display_name}")
                    video_path = await download_video(youtube_url)
                    video_analyzer = VideoAnalyzer(video_path)
                    skip_first_frames = 100
                    skip_first_frames = skip_first_frames if skip_first_frames > video_analyzer.frame_count else 0 
                    training_frame_count = int(video_analyzer.frame_count * 0.8)
                    stable_rectangle = await video_analyzer.find_stable_rectangle(training_frame_count, skip_first_frames)

                    if not stable_rectangle:
                        embed.title = "❌ Training Fehlgeschlagen"
                        embed.description = "Bitte wende dich an Pfeffermuehle"
                        embed.color = discord.Color.red()
                        log.warning(f"Training Fehlgeschlagen für {message.author.display_name}")
                        await message.remove_reaction("⏳", bot.user)
                        await message.add_reaction("❌")
                        await message.channel.send(embed=embed)
                        return

                    async def send_progress_update(processed: int, total: int):
                        log.info(f"Fortschritt: {processed} von {total} Frames analysiert.")

                    timestamps = await video_analyzer.analyze_video(stable_rectangle, send_progress_update)

                    mot_message = await get_feedback_message(len(timestamps))

                    embed = discord.Embed()
                    embed.title = f"✅ Analyse abgeschlossen! für {youtube_url}"
                    embed.description = f"⏱ **An Folgenden Stellen bist du Out Of Stamina:**\n"
                    embed.color = discord.Color.green()

                    # Liste für die drei Gruppen
                    fields = ["", "", ""]
                    # Alle Timestamps durchgehen und in die passende Gruppe einordnen
                    num_groups = max(1, len(timestamps) // 3)  # Verhindert Division durch 0

                    for index, timestamp in enumerate(timestamps, start=1):
                        group_index = (index - 1) // num_groups  # Bestimmt die Gruppe (0, 1 oder 2)
                        group_index = min(group_index, 2)  # Falls `remaining_items` existiert, Begrenzung auf max. 2
                        fields[group_index] += f"**#{index}.** {timestamp}\n"

                    # Felder zum Embed hinzufügen
                    for field_content in fields:
                        if len(field_content) < 1000:
                            embed.add_field(name="", value=field_content, inline=True)
                        else:
                            embed.add_field(name="Keine Ausgabe", value="Du bist zu oft out of stamina, (message ist zu groß zum senden!)")
                            embed.color = discord.Color.red()

                    embed.add_field(name="", value=mot_message)

                    if channel_hidden == False:
                        await message.channel.send(embed=embed)
                        await message.remove_reaction("⏳", bot.user)
                        await message.add_reaction("✅")
                    else:
                        if coach_channel:
                            embed.title = f"Neues VOD von {message.author.display_name}"
                            await coach_channel.send(embed=embed)

                    log.info(f"Fertig mit VOD hidden for {message.author.display_name}")
                    return
            except yt_dlp.utils.DownloadError as e:
                log.error(f"Download Error: {str(e)}")
            
            await asyncio.sleep(120)
            log.info(f"Putting VOD {message.id} from {message.author.display_name} back into queue.")
            stamina_queue.append(message.id)

        log.warning(f"Max Retries reached for VOD from {message.author.display_name}, dropping VOD.")
        if message.id in stamina_queue:
            stamina_queue.remove(message.id)

        embed = discord.Embed()
        embed.title = f"❌ Dein video ist entweder noch nicht hochgeladen oder noch nicht verarbeitet von youtube! {youtube_url}"
        embed.description = f"Dein VOD wird übersprungen, nutze `/stamina_check {youtube_url}` um es nochmal manuel zu versuchen."
        embed.color = discord.Color.red()

        if not channel_hidden:
            await message.channel.send(embed=embed)
        else:
            if coach_channel:
                embed.title = f"❌ Dein video ist entweder noch nicht hochgeladen oder noch nicht verarbeitet von youtube! {youtube_url}, CC=hidden~<make install_module hidden>~WARNING>>\\x06\\x01\\xA0\\x00 User: {message.author.display_name} ~WENN DU DAS HIER SIEHST MELDE DICH BEI PFEFFERMUEHLE! JETZT!~"
                await coach_channel.send(embed=embed)

@bot.event
async def on_voice_state_update(member, before, after):
    """Track voice channel activity for XP rewards"""
    if member.bot:
        return
    
    # User joined a voice channel
    if before.channel is None and after.channel is not None:
        # Check if user can speak in the channel
        can_speak = await can_user_speak_in_channel(member, after.channel)
        await start_voice_session(member.id, after.channel.id, after.self_mute, after.self_deaf, after.mute, after.deaf, can_speak)
    
    # User left a voice channel
    elif before.channel is not None and after.channel is None:
        leveled_up, new_level = await end_voice_session(member.id, before.channel.id, member.display_name)
        
        # Update nickname if level up occurred without notification
        if leveled_up:
            await update_member_nickname(member)
    
    # User switched voice channels
    elif before.channel is not None and after.channel is not None and before.channel.id != after.channel.id:
        # End previous session
        await end_voice_session(member.id, before.channel.id, member.display_name)
        # Start new session
        can_speak = await can_user_speak_in_channel(member, after.channel)
        await start_voice_session(member.id, after.channel.id, after.self_mute, after.self_deaf, after.mute, after.deaf, can_speak)
    
    # User mute/deaf status changed
    elif before.channel is not None and after.channel is not None and (before.self_mute != after.self_mute or 
                                                                       before.self_deaf != after.self_deaf or
                                                                       before.mute != after.mute or
                                                                       before.deaf != after.deaf):
        # Update mute/deaf status in the tracker
        if member.id in voice_activity_tracker and str(before.channel.id) in voice_activity_tracker[member.id]:
            voice_activity_tracker[member.id][str(before.channel.id)]["is_muted"] = after.self_mute or after.mute
            voice_activity_tracker[member.id][str(before.channel.id)]["is_deafened"] = after.self_deaf or after.deaf
            
            log_message = f"User {member.display_name} status changed in channel {before.channel.name}: "
            if after.self_mute:
                log_message += "self-muted "
            if after.mute:
                log_message += "server-muted "
            if after.self_deaf:
                log_message += "self-deafened "
            if after.deaf:
                log_message += "server-deafened "
            if not any([after.self_mute, after.mute, after.self_deaf, after.deaf]):
                log_message += "all clear "
            
            log.info(log_message)

async def can_user_speak_in_channel(member, channel):
    """Check if a user can speak in a voice channel"""
    # Check if channel is a voice channel
    if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
        return False
    
    # Check channel type
    if isinstance(channel, discord.StageChannel) and not member.guild_permissions.administrator:
        # In stage channels, only speakers can talk
        # By default, assume they're audience in a stage channel
        return False
    
    # Check permissions
    permissions = channel.permissions_for(member)
    return permissions.speak and permissions.connect

# Function to track when a user speaks in voice
async def record_user_voice_activity(user_id, channel_id):
    """
    Record that a user spoke in a voice channel.
    
    Note: This function is currently only called when a user sends a text message while in a voice channel,
    not when they actually speak. Discord's API doesn't provide a direct event for voice activity.
    To track actual speaking, you would need to use Discord's voice API and process audio data.
    """
    if user_id in voice_activity_tracker and str(channel_id) in voice_activity_tracker[user_id]:
        # Only record if they're allowed to speak
        if voice_activity_tracker[user_id][str(channel_id)].get("can_speak", True) and not voice_activity_tracker[user_id][str(channel_id)].get("is_muted", False):
            # Update the last time the user spoke
            voice_activity_tracker[user_id][str(channel_id)]["last_spoke"] = int(time.time())
            log.info(f"User {user_id} spoke in channel {channel_id}")
        else:
            log.info(f"User {user_id} tried to speak in channel {channel_id} but is muted or can't speak")

async def start_voice_session(user_id, channel_id, is_self_muted=False, is_self_deafened=False, is_server_muted=False, is_server_deafened=False, can_speak=True):
    """Record start of voice activity"""
    # Store in memory for quick access
    if user_id not in active_voice_users:
        active_voice_users[user_id] = {}
    
    if user_id not in voice_activity_tracker:
        voice_activity_tracker[user_id] = {}
    
    # Only start if not already in this channel
    if channel_id not in active_voice_users[user_id]:
        start_time = int(time.time())
        # Store as dictionary instead of just the timestamp
        active_voice_users[user_id][channel_id] = {
            "start_time": start_time,
            "was_active": False
        }
        
        # Initialize voice activity tracking
        voice_activity_tracker[user_id][str(channel_id)] = {
            "start_time": start_time,
            "last_spoke": start_time,  # Assume they speak when joining for initial state
            "is_muted": is_self_muted or is_server_muted,
            "is_deafened": is_self_deafened or is_server_deafened,
            "can_speak": can_speak
        }
        
        # Store in database
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO voice_sessions (user_id, channel_id, start_time) VALUES (?, ?, ?)",
            (str(user_id), str(channel_id), start_time)
        )
        conn.commit()
        conn.close()

async def end_voice_session(user_id, channel_id, username):
    """End user voice session and award XP"""
    if user_id in active_voice_users and channel_id in active_voice_users[user_id]:
        # Get session info
        session_info = active_voice_users[user_id][channel_id]
        start_time = session_info["start_time"]
        was_active = session_info.get("was_active", False)
        
        # Calculate duration in seconds
        now = int(time.time())
        duration = now - start_time
        
        # Clean up tracking dictionaries
        if user_id in voice_activity_tracker and str(channel_id) in voice_activity_tracker[user_id]:
            del voice_activity_tracker[user_id][str(channel_id)]
            if not voice_activity_tracker[user_id]:
                del voice_activity_tracker[user_id]
        
        # Remove from active sessions
        del active_voice_users[user_id][channel_id]
        if not active_voice_users[user_id]:
            del active_voice_users[user_id]
        
        # Update database
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Mark session as inactive
        cursor.execute(
            "UPDATE voice_sessions SET is_active = 0 WHERE user_id = ? AND channel_id = ? AND start_time = ?",
            (str(user_id), str(channel_id), start_time)
        )
        
        # Update voice time
        cursor.execute(
            "UPDATE user_levels SET voice_time = voice_time + ? WHERE user_id = ?",
            (duration, str(user_id))
        )
        conn.commit()
        conn.close()
        
        # Award XP only if user was active
        if was_active:
            # Award XP (3 XP per minute with a minimum of 1)
            minutes = max(1, int(duration / 60))
            xp_earned = minutes * 3
            leveled_up, new_level = await add_xp(user_id, username, xp_earned, reason="voice")
            
            # If level up occurred, update nickname without notification
            if leveled_up:
                # Find the guild and member object
                channel = bot.get_channel(int(channel_id))
                if channel and channel.guild:
                    member = channel.guild.get_member(int(user_id))
                    if member:
                        await update_member_nickname(member)
            
            return leveled_up, new_level
        
        return False, None
    
    return False, None

def parse_changelog():
    try:
        with open('CHANGELOG.txt', 'r') as file:
            content = file.read()
            end_index = content.find('---')
            if end_index != -1:
                latest_entry = content[:end_index].strip()
            else:
                latest_entry = content.strip()
            return latest_entry
    except FileNotFoundError:
        return "CHANGELOG.txt file not found."
    except Exception as e:
        return f"Error reading the changelog: {str(e)}"
    
@tree.command(name="changelog", description="Gives you the latest changelog entry.")
async def changelog(ctx: discord.Interaction):
    latest_changelog = parse_changelog()
    embed = discord.Embed(title="Latest Changelog", description=f"[View the full changelog here](https://github.com/Katze719/new-world-stamina-checker/blob/main/CHANGELOG.txt)\n\n{latest_changelog}", color=discord.Color.blue())
    if len(latest_changelog) > 2000:
        latest_changelog = latest_changelog[:2000] + "..."


    # Get specific user from guild where command was executed
    target_user = ctx.guild.get_member(617685482331045908)
    if target_user:
        embed.set_author(name=f"von eurer {target_user.display_name}")
        if target_user.avatar:
            embed.set_thumbnail(url=target_user.avatar.url)
    await ctx.response.send_message(embed=embed)

def pattern_to_regex(pattern: str) -> re.Pattern:
    """
    Erzeugt aus dem Pattern einen Regex.
    Ersetzt {name}, {level} und {icons} durch nicht-gierige Capturing-Gruppen.
    """
    escaped = re.escape(pattern)
    escaped = escaped.replace(re.escape("{name}"), r"(?P<name>.*?)")
    escaped = escaped.replace(re.escape("{level}"), r"(?P<level>.*?)")
    escaped = escaped.replace(re.escape("{icons}"), r"(?P<icons>.*?)")
    return re.compile("^" + escaped + "$")

def get_level_emoji(level: int) -> str:
    """Returns the string representation of a level number."""
    # Für alle Level geben wir einfach die Zahl als String zurück
    return str(level)

async def update_member_nickname(member: discord.Member):
    """
    Aktualisiert den Nickname eines Members anhand der globalen Einstellungen.
    Es werden alle in role_settings gespeicherten Icons kombiniert
    und das globale Pattern angewandt.
    """
    role_settings = role_name_update_settings_cache.get("role_settings", {})
    icons_with_prio = []
    for role in member.roles:
        role_id = str(role.id)
        if role_id in role_settings:
            settings = role_settings[role_id]
            icon = settings.get("icon", "")
            prio = settings.get("prio", 0)  # Standard-Priorität, falls keine gesetzt ist
            icons_with_prio.append((prio, icon))

    # Beispiel: Wenn eine höhere Zahl eine höhere Priorität bedeutet,
    # sortiere absteigend, sodass das Icon mit der höchsten Priorität links steht.
    icons_with_prio.sort(key=lambda x: x[0], reverse=True)
    
    # Falls bei dir eine niedrigere Zahl eine höhere Priorität bedeutet,
    # sortiere stattdessen aufsteigend:
    # icons_with_prio.sort(key=lambda x: x[0])
    
    icons = "".join(icon for prio, icon in icons_with_prio)
    
    # Get user's level emoji
    level_data = await get_user_level_data(member.id)
    level = 0
    if level_data:
        level = level_data["level"]
    level_emoji = get_level_emoji(level)
    
    pattern = role_name_update_settings_cache.get("global_pattern", default_pattern)
    regex = pattern_to_regex(pattern)
    match = regex.match(member.display_name)
    if match:
        try:
            base_name = match.group("name").strip()
        except IndexError:
            base_name = member.display_name
    else:
        base_name = member.display_name

    expected_nick = pattern.format(icons=icons, name=base_name, level=level_emoji)
    if len(expected_nick) > 32:
        # Berechne, wie viele Zeichen für {name} übrig bleiben, wenn {icons} und {level} unverändert bleiben
        fixed_part = pattern.format(icons=icons, name="", level=level_emoji)  # Platzhalter für den variablen Teil wird hier durch "" ersetzt
        allowed_name_length = 32 - len(fixed_part)
        if allowed_name_length < 0:
            allowed_name_length = 0  # Sicherheitshalber
        base_name = base_name[:allowed_name_length]
        expected_nick = pattern.format(icons=icons, name=base_name, level=level_emoji)

    if member.display_name != expected_nick:
        try:
            log.info(f"Nickname von {member.display_name} geändert zu {expected_nick}")
            await member.edit(nick=expected_nick)
        except discord.Forbidden:
            log.error(f"Konnte Nickname von {member.display_name} nicht ändern - fehlende Berechtigungen.")
        except discord.NotFound:
            log.error(f"Member {member} wurde nicht gefunden (vermutlich gekickt).")

async def migrate_nickname(member: discord.Member):
    """
    Spezielle Funktion für die Migration vom alten Format "Name [icons]" zum neuen Format "Name (level) [icons]".
    Erkennt das alte Format und extrahiert korrekt den Namen, ohne die Icons zu duplizieren.
    """
    # Prüfe zuerst, ob der Name bereits das neue Format hat
    pattern = role_name_update_settings_cache.get("global_pattern", default_pattern)
    regex = pattern_to_regex(pattern)
    if regex.match(member.display_name):
        # Bereits im neuen Format, keine Änderung notwendig
        return

    # Extrahiere Icons aus den Rollen
    role_settings = role_name_update_settings_cache.get("role_settings", {})
    icons_with_prio = []
    for role in member.roles:
        role_id = str(role.id)
        if role_id in role_settings:
            settings = role_settings[role_id]
            icon = settings.get("icon", "")
            prio = settings.get("prio", 0)
            icons_with_prio.append((prio, icon))
    
    icons_with_prio.sort(key=lambda x: x[0], reverse=True)
    icons = "".join(icon for prio, icon in icons_with_prio)
    
    # Get user's level emoji
    level_data = await get_user_level_data(member.id)
    level = 0
    if level_data:
        level = level_data["level"]
    level_emoji = get_level_emoji(level)
    
    # Regex für das alte Format "Name [icons]"
    old_format_regex = re.compile(r"^(.*?)\s*\[(.*?)\]$")
    match = old_format_regex.match(member.display_name)
    
    if match:
        # Altes Format erkannt - extrahiere nur den Namen ohne Icons
        base_name = match.group(1).strip()
    else:
        # Falls kein bekanntes Format, nimm den ganzen Namen
        base_name = member.display_name
    
    # Wende neues Format an
    expected_nick = pattern.format(icons=icons, name=base_name, level=level_emoji)
    
    # Längenbegrenzung beachten
    if len(expected_nick) > 32:
        fixed_part = pattern.format(icons=icons, name="", level=level_emoji)
        allowed_name_length = 32 - len(fixed_part)
        if allowed_name_length < 0:
            allowed_name_length = 0
        base_name = base_name[:allowed_name_length]
        expected_nick = pattern.format(icons=icons, name=base_name, level=level_emoji)
    
    # Aktualisiere den Nicknamen
    if member.display_name != expected_nick:
        try:
            log.info(f"Migration: Nickname von {member.display_name} geändert zu {expected_nick}")
            await member.edit(nick=expected_nick)
        except discord.Forbidden:
            log.error(f"Konnte Nickname von {member.display_name} nicht ändern - fehlende Berechtigungen.")
        except discord.NotFound:
            log.error(f"Member {member} wurde nicht gefunden (vermutlich gekickt).")

@tree.command(name="migrate_all_users", description="Migriert alle Nutzernamen vom alten Format zum neuen Format mit Level")
@app_commands.checks.has_permissions(administrator=True)
async def migrate_all_users(interaction: discord.Interaction):
    """
    Migriert alle Nutzernamen vom alten Format 'Name [icons]' zum neuen Format 'Name (level) [icons]',
    ohne die Icons zu duplizieren. Nutzt den speziellen Migrationsalgorithmus.
    """
    await interaction.response.send_message("Migriere alle Nutzernamen vom alten zum neuen Format...", ephemeral=True)
    guild = interaction.guild
    
    if guild:
        migrated_count = 0
        already_migrated = 0
        
        # Aktuelles Pattern für die Erkennung bereits migrierter Nutzer
        pattern = role_name_update_settings_cache.get("global_pattern", default_pattern)
        regex = pattern_to_regex(pattern)
        
        # Altes Format für die Erkennung zu migrierender Nutzer
        old_format_regex = re.compile(r'^(.*?)\s*\[.*\]$')
        
        for member in guild.members:
            # Prüfe, ob bereits im neuen Format
            if regex.match(member.display_name):
                already_migrated += 1
                await update_member_in_spreadsheet(member)
                continue
                
            # Alte Namen im Format "Name [icons]" migrieren
            if old_format_regex.match(member.display_name):
                await migrate_nickname(member)
                migrated_count += 1
            else:
                # Normale Aktualisierung für andere Namen
                await update_member_nickname(member)
            
            await update_member_in_spreadsheet(member)
    
    await spreadsheet.memberlist.sort_member(spreadsheet_acc, spreadsheet_role_settings_manager)
    await interaction.edit_original_response(
        content=f"Migration abgeschlossen!\n"
               f"• {migrated_count} Nutzernamen wurden vom alten Format migriert\n"
               f"• {already_migrated} Nutzer waren bereits im neuen Format"
    )

async def update_member_in_spreadsheet(member: discord.Member):
    def parse_name(member : discord.Member):
        pattern = role_name_update_settings_cache.get("global_pattern", default_pattern)
        regex = pattern_to_regex(pattern)
        match = regex.match(member.display_name)
        if match:
            try:
                return match.group("name").strip()
            except (IndexError, KeyError):
                return member.display_name
        else:
            # Fallback: Versuche es mit dem alten Pattern ohne Level
            old_pattern = "{name} [{icons}]"
            old_regex = pattern_to_regex(old_pattern)
            old_match = old_regex.match(member.display_name)
            if old_match:
                try:
                    return old_match.group("name").strip()
                except (IndexError, KeyError):
                    pass
            
            # Einfacher Fallback: Suche nach Name vor eckigen Klammern
            bracket_match = re.match(r'^(.*?)\s*\[.*\]$', member.display_name)
            if bracket_match:
                return bracket_match.group(1).strip()
                
            return member.display_name

    await spreadsheet.memberlist.update_member(spreadsheet_acc, member, parse_name, spreadsheet_role_settings_manager)

@bot.event
async def on_member_update(before, after: discord.Member):
    """
    Wird aufgerufen, wenn sich ein Member ändert und wendet die globalen Regeln an.
    """
    await update_member_nickname(after)
    await update_member_in_spreadsheet(after)
    await spreadsheet.memberlist.sort_member(spreadsheet_acc, spreadsheet_role_settings_manager)

@tree.command(name="set_role", description="Setze Icon und/oder Priorität für eine Rolle")
@app_commands.describe(
    role="Wähle die Rolle aus, für die die Einstellungen gesetzt werden sollen",
    icon="Das Icon, das vor dem Benutzernamen angezeigt werden soll (optional)",
    prio="Die Priorität der Rolle (optional, Standard: 0)"
)
@app_commands.checks.has_permissions(administrator=True)
async def set_role(interaction: discord.Interaction, role: discord.Role, icon: Optional[str] = None, prio: Optional[int] = None):
    """
    Administratoren können pro Rolle ein Icon und/oder eine Priorität festlegen.
    Das Icon wird später mit dem globalen Pattern kombiniert.
    """
    global role_name_update_settings_cache
    if "role_settings" not in role_name_update_settings_cache:
        role_name_update_settings_cache["role_settings"] = {}
    
    # Bestehende Einstellungen abrufen oder ein leeres Dictionary anlegen
    settings = role_name_update_settings_cache["role_settings"].get(str(role.id), {})
    
    # Nur setzen, wenn ein Wert übergeben wurde
    if icon is not None:
        settings["icon"] = icon
    if prio is not None:
        settings["prio"] = prio

    role_name_update_settings_cache["role_settings"][str(role.id)] = settings
    await settings_manager.save(role_name_update_settings_cache)
    await interaction.response.send_message(f"Einstellungen für Rolle **{role.name}** wurden gespeichert.", ephemeral=True)
    
@tree.command(name="clear_role", description="Entferne das Icon für eine Rolle")
@app_commands.describe(
    role="Wähle die Rolle aus, für die das Icon entfernt werden soll"
)
@app_commands.checks.has_permissions(administrator=True)
async def clear_role(interaction: discord.Interaction, role: discord.Role):
    """
    Administratoren können das Icon für eine Rolle entfernen.
    Dadurch wird das Icon nicht mehr in den globalen Nickname eingefügt.
    """
    global role_name_update_settings_cache
    if "role_settings" not in role_name_update_settings_cache:
        role_name_update_settings_cache["role_settings"] = {}
    
    if str(role.id) in role_name_update_settings_cache["role_settings"]:
        del role_name_update_settings_cache["role_settings"][str(role.id)]
        await settings_manager.save(role_name_update_settings_cache)
        await interaction.response.send_message(f"Icon für Rolle **{role.name}** wurde entfernt.", ephemeral=True)
    else:
        await interaction.response.send_message(f"Für Rolle **{role.name}** war kein Icon gesetzt.", ephemeral=True)
    

@tree.command(name="set_pattern", description="Setze das globale Namensmuster")
@app_commands.describe(
    pattern="Das Namensmuster, z.B. '[{icons}] {name}' (Platzhalter: {icons} für kombinierte Icons, {name} für den ursprünglichen Namen)"
)
@app_commands.checks.has_permissions(administrator=True)
async def set_pattern(interaction: discord.Interaction, pattern: str):
    """
    Administratoren können das globale Namensmuster festlegen.
    Dieses Muster wird für alle Mitglieder verwendet, die Icons zugewiesen haben.
    """
    global role_name_update_settings_cache
    role_name_update_settings_cache["global_pattern"] = pattern
    await settings_manager.save(role_name_update_settings_cache)
    await interaction.response.send_message(f"Globales Namensmuster wurde auf `{pattern}` gesetzt.", ephemeral=True)
    
@set_pattern.autocomplete("pattern")
async def pattern_autocomplete(interaction: discord.Interaction, current: str):
    """
    Gibt eine Auswahl an bereits genutzten Patterns zurück.
    (Hier kannst du z. B. eine statische Liste oder eine aus der Datenbank geladene Liste verwenden.)
    """
    used_patterns = [
        "{name} ({level}) [{icons}]",
    ]
    choices = []
    for p in used_patterns:
        if current.lower() in p.lower():
            choices.append(app_commands.Choice(name=p, value=p))
    return choices

@tree.command(name="update_all_users", description="Updatet alle Icons bei jedem Nutzer")
@app_commands.checks.has_permissions(administrator=True)
async def update_all_users(interaction: discord.Interaction):
    # Wende das neue Pattern auf alle Mitglieder an
    await interaction.response.send_message("Update alle Nutzer ...", ephemeral=True)
    guild = interaction.guild
    if guild:
        for member in guild.members:
            await update_member_nickname(member)
            await update_member_in_spreadsheet(member)

    await spreadsheet.memberlist.sort_member(spreadsheet_acc, spreadsheet_role_settings_manager)
    await interaction.edit_original_response(content="Nutzer wurden Aktualisiert!")
    
@tree.command(name="list_roles", description="Zeigt alle Rollen mit Icon und Priorität an")
@app_commands.checks.has_permissions(administrator=True)
async def list_roles(interaction: discord.Interaction):
    """
    Zeigt alle in den Einstellungen konfigurierten Rollen an, inklusive Icon und Priorität.
    Falls mehr als 25 Rollen konfiguriert sind, wird die Liste in mehrere Seiten (Embeds) aufgeteilt,
    zwischen denen per Buttons navigiert werden kann.
    """
    global role_name_update_settings_cache
    role_settings = role_name_update_settings_cache.get("role_settings", {})

    if not role_settings:
        await interaction.response.send_message("Es sind keine Rollen-Einstellungen vorhanden.", ephemeral=True)
        return

    # Erstelle eine Liste mit (Rollenname, Icon, Priorität)
    roles_list = []
    for role_id, settings in role_settings.items():
        role_obj = interaction.guild.get_role(int(role_id))
        role_name = role_obj.name if role_obj else f"Unbekannte Rolle ({role_id})"
        icon = settings.get("icon", "")
        prio = settings.get("prio", 0)
        roles_list.append((role_name, icon, prio))

    # Sortiere die Rollen nach Priorität (absteigend)
    roles_list.sort(key=lambda x: x[2], reverse=True)

    # Teile die Liste in Seiten zu je 5 Einträgen auf
    pages = []
    items_per_page = 5
    for i in range(0, len(roles_list), items_per_page):
        page_roles = roles_list[i:i + items_per_page]
        embed = discord.Embed(
            title="Rollen Einstellungen",
            description="Konfigurierte Rollen mit ihren Icons und Prioritäten:",
            color=discord.Color.blue()
        )
        for role_name, icon, prio in page_roles:
            embed.add_field(name=role_name, value=f"- Icon: {icon}\n- Priorität: {prio}", inline=False)
        embed.set_footer(text=f"Seite {i // items_per_page + 1} von {((len(roles_list) - 1) // items_per_page) + 1}")
        pages.append((embed, page_roles))

    # Falls es nur eine Seite gibt, sende diese mit den Rollen-Buttons
    if len(pages) == 1:
        view = RoleButtonsView(pages[0][1])
        await interaction.response.send_message(embed=pages[0][0], view=view, ephemeral=True)
        return

    # Bei mehreren Seiten wird der Paginator verwendet
    view = Paginator(pages)
    await interaction.response.send_message(embed=pages[0][0], view=view, ephemeral=True)


# Die Buttons für die Rollen, die den Modal öffnen
class RoleSettingsButton(discord.ui.Button):
    def __init__(self, role_name, icon, prio):
        super().__init__(label=role_name, style=discord.ButtonStyle.secondary)
        self.role_name = role_name
        self.icon = icon
        self.prio = prio

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(RoleSettingsInput(self.role_name, self.icon, self.prio))


# Der Modal, in dem die Rolleneinstellungen bearbeitet werden
class RoleSettingsInput(discord.ui.Modal, title="Rollen Einstellungen"):
    def __init__(self, role_name, icon, prio):
        super().__init__()
        self.role_name = role_name
        self.icon_input = discord.ui.TextInput(label="Icon", default=icon, required=False)
        self.prio_input = discord.ui.TextInput(label="Priorität", default=str(prio), required=False)
        self.add_item(self.icon_input)
        self.add_item(self.prio_input)

    async def on_submit(self, interaction: discord.Interaction):
        global role_name_update_settings_cache
        role_settings = role_name_update_settings_cache.get("role_settings", {})
        for role_id, settings in role_settings.items():
            role_obj = interaction.guild.get_role(int(role_id))
            if role_obj and role_obj.name == self.role_name:
                settings["icon"] = self.icon_input.value
                settings["prio"] = int(self.prio_input.value)
                role_name_update_settings_cache["role_settings"][str(role_obj.id)] = settings
                await settings_manager.save(role_name_update_settings_cache)
                await interaction.response.send_message(f"Einstellungen für Rolle **{self.role_name}** wurden aktualisiert.", ephemeral=True)
                guild = interaction.guild
                if guild:
                    await post_icons_to_channel(interaction)
                    for member in guild.members:
                        await update_member_nickname(member)
                return
        await interaction.response.send_message(f"Rolle **{self.role_name}** nicht gefunden.", ephemeral=True)


# View, die nur für eine einzelne Seite genutzt wird
class RoleButtonsView(discord.ui.View):
    def __init__(self, roles):
        super().__init__(timeout=None)
        for role_name, icon, prio in roles:
            self.add_item(RoleSettingsButton(role_name, icon, prio))


# Paginator, der die Buttons und Navigation kombiniert
class Paginator(discord.ui.View):
    def __init__(self, pages, timeout=None):
        super().__init__(timeout=timeout)
        self.pages = pages
        self.current_page = 0
        self.update_view()

    def update_view(self):
        self.clear_items()
        embed, roles = self.pages[self.current_page]
        # Füge alle Rollen-Buttons der aktuellen Seite hinzu
        for role_name, icon, prio in roles:
            self.add_item(RoleSettingsButton(role_name, icon, prio))
        # Füge die Navigations-Buttons hinzu
        self.add_item(PreviousButton(self))
        self.add_item(NextButton(self))

    async def update_message(self, interaction: discord.Interaction):
        embed, _ = self.pages[self.current_page]
        self.update_view()
        await interaction.response.edit_message(embed=embed, view=self)


class PreviousButton(discord.ui.Button):
    def __init__(self, paginator):
        super().__init__(label="< Vorherige", style=discord.ButtonStyle.primary)
        self.paginator = paginator

    async def callback(self, interaction: discord.Interaction):
        if self.paginator.current_page > 0:
            self.paginator.current_page -= 1
            await self.paginator.update_message(interaction)
        else:
            await interaction.response.defer()


class NextButton(discord.ui.Button):
    def __init__(self, paginator):
        super().__init__(label="Nächste >", style=discord.ButtonStyle.primary)
        self.paginator = paginator

    async def callback(self, interaction: discord.Interaction):
        if self.paginator.current_page < len(self.paginator.pages) - 1:
            self.paginator.current_page += 1
            await self.paginator.update_message(interaction)
        else:
            await interaction.response.defer()

async def extractUsers(message: discord.Message):
    # check if user sent a image
    if not message.attachments:
        return []
    
    # download image
    attachment = message.attachments[0]
    image_path = os.path.join("temp", attachment.filename)
    os.makedirs("temp", exist_ok=True)
    await attachment.save(image_path)

    guild = message.guild
    if guild:
        # create list with user nicknames
        nicknames = []
        for member in guild.members:
            pattern = role_name_update_settings_cache.get("global_pattern", default_pattern)
            regex = pattern_to_regex(pattern)
            match = regex.match(member.display_name)
            if match:
                base_name = match.group("name").strip()
            else:
                base_name = member.display_name
            nicknames.append(base_name)

        # extract users from image
        users = textExtract.extractNamesFromImage(image_path, nicknames)
        os.remove(image_path)
        return users
    
    #delete tmp file
    os.remove(image_path)
    return []

@app_commands.checks.has_permissions(administrator=True)
@tree.command(name="watch_this_for_user_extraction", description="Extrahiert User aus Bildern in diesem Channel")
async def watch_this_channel_user_extraction(interaction: discord.Interaction):
    channels = await gp_channel_manager.load()
    channel_id = str(interaction.channel.id)

    if channel_id == channels.get("watch_user_exctaction_channel", None):
        await interaction.response.send_message("Dieser Channel ist bereits in der User-Extraktionsliste.", ephemeral=True)
        return

    channels["watch_user_exctaction_channel"] = channel_id
    await gp_channel_manager.save(channels)
    await interaction.response.send_message(
        f"Channel wurde erfolgreich zur User-Extraktionsliste hinzugefügt!",
        ephemeral=True
    )

@app_commands.checks.has_permissions(administrator=True)
@tree.command(name="remove_this_from_user_extraction", description="Entferne diesen Channel von der User-Extraktionsliste")
async def remove_this_channel_user_extraction(interaction: discord.Interaction):
    channels = await gp_channel_manager.load()
    channel_id = str(interaction.channel.id)

    if channel_id != channels.get("watch_user_exctaction_channel", None):
        await interaction.response.send_message("Dieser Channel ist nicht in der User-Extraktionsliste.", ephemeral=True)
        return

    channels["watch_user_exctaction_channel"] = None
    await gp_channel_manager.save(channels)
    await interaction.response.send_message("Channel wurde erfolgreich von der User-Extraktionsliste entfernt!", ephemeral=True)

@app_commands.checks.has_permissions(administrator=True)
@tree.command(name="set_check_channel", description="Checkt den channel alle 5 Minuten, wenn nach 1 stunnde keine Nachricht")
async def set_check_channel(interaction: discord.Interaction, role: discord.Role):
    channels = await gp_channel_manager.load()
    channel_id = str(interaction.channel.id)

    h = channels.get("send_hour_channel") or {}

    if channel_id == h.get("channel_id", None):
        await interaction.response.send_message("Dieser Channel ist bereits in der User-Extraktionsliste.", ephemeral=True)
        return
    
    channels["send_hour_channel"] = {
        "channel_id": channel_id,
        "role_id": role.id
    }
    await gp_channel_manager.save(channels)
    await interaction.response.send_message(
        f"Channel wurde erfolgreich zur User-Extraktionsliste hinzugefügt!",
        ephemeral=True
    )

@app_commands.checks.has_permissions(administrator=True)
@tree.command(name="remove_check_channel", description="Entferne diesen Channel von der User-Extraktionsliste")
async def remove_check_channel(interaction: discord.Interaction):
    channels = await gp_channel_manager.load()
    channel_id = str(interaction.channel.id)

    h = channels.get("send_hour_channel") or {}

    if channel_id != h.get("channel_id", None):
        await interaction.response.send_message("Dieser Channel ist nicht in der User-Extraktionsliste.", ephemeral=True)
        return
    
    channels["send_hour_channel"] = {}
    await gp_channel_manager.save(channels)
    await interaction.response.send_message("Channel wurde erfolgreich von der User-Extraktionsliste entfernt!", ephemeral=True)

@tasks.loop(minutes=5)
async def check_channel():
    channels = await gp_channel_manager.load()
    send_hour_channel_dict = channels.get("send_hour_channel") or {}
    channel = bot.get_channel(int(send_hour_channel_dict.get("channel_id", 0)))
    if channel is None:
        return

    # Aktuelle Zeit in der deutschen Zeitzone (Europe/Berlin)
    jetzt = datetime.datetime.now(ZoneInfo("Europe/Berlin"))
    aktuelle_stunde = jetzt.hour

    # Kein Ping zwischen 22:00 und 08:00 Uhr (deutsche Zeit)
    if aktuelle_stunde >= 22 or aktuelle_stunde < 8:
        return

    # Letzte Nachricht im Channel abrufen
    letzte_nachricht = None
    async for message in channel.history(limit=1):
        letzte_nachricht = message

    if letzte_nachricht:
        # Die Nachricht-Zeit wird in UTC gespeichert; umwandeln in Europe/Berlin
        message_time = letzte_nachricht.created_at.astimezone(ZoneInfo("Europe/Berlin"))
        zeit_diff = (jetzt - message_time).total_seconds()
        if zeit_diff > 3600 * 2:  # mehr als 1 Stunde (3600 Sekunden)
            role = channel.guild.get_role(send_hour_channel_dict.get("role_id", None))
            if role:
                await channel.send(f"{role.mention} ey hier ist mal wieder ziemlich ruhig, wir wollen wachsen wir brauchen Werbung! Jeder darf Werbung machen also abfahrt!")

@tasks.loop(minutes=30)
async def check_for_raidhelpers():
    log.info("Checking for raid helpers")
    def parse_name(member : discord.Member):
        pattern = role_name_update_settings_cache.get("global_pattern", default_pattern)
        regex = pattern_to_regex(pattern)
        match = regex.match(member.display_name)
        if match:
            try:
                return match.group("name").strip()
            except (IndexError, KeyError):
                return member.display_name
        else:
            # Fallback: Versuche es mit dem alten Pattern ohne Level
            old_pattern = "{name} [{icons}]"
            old_regex = pattern_to_regex(old_pattern)
            old_match = old_regex.match(member.display_name)
            if old_match:
                try:
                    return old_match.group("name").strip()
                except (IndexError, KeyError):
                    pass
            
            # Einfacher Fallback: Suche nach Name vor eckigen Klammern
            bracket_match = re.match(r'^(.*?)\s*\[.*\]$', member.display_name)
            if bracket_match:
                return bracket_match.group(1).strip()
                
            return member.display_name

    await spreadsheet.payoutlist.update_payoutlist(bot, spreadsheet_acc, parse_name, spreadsheet_role_settings_manager, gp_channel_manager)

@tree.command(name="test", description="Test Command")
async def test(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    await interaction.edit_original_response(content=f"Your user ID is: {interaction.user.id}")

    # def parse_name(member : discord.Member):
    #     pattern = role_name_update_settings_cache.get("global_pattern", default_pattern)
    #     regex = pattern_to_regex(pattern)
    #     match = regex.match(member.display_name)
    #     if match:
    #         return match.group("name").strip()
    #     else:
    #         return member.display_name

    # await spreadsheet.payoutlist.update_payoutlist(bot, spreadsheet_acc, parse_name, spreadsheet_role_settings_manager, gp_channel_manager)
    # await interaction.edit_original_response(content="Nutzer wurden Aktualisiert!")

    # def parse_name(member : discord.Member):
    #     pattern = role_name_update_settings_cache.get("global_pattern", default_pattern)
    #     regex = pattern_to_regex(pattern)
    #     match = regex.match(member.display_name)
    #     if match:
    #         return match.group("name").strip()
    #     else:
    #         return member.display_name

    # await spreadsheet.stats.stats(spreadsheet_acc, interaction, parse_name, spreadsheet_role_settings_manager)

@tree.command(name="stats", description="Zeigt deine Stats aus dem google sheet")
async def stats(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    def parse_name(member : discord.Member):
        pattern = role_name_update_settings_cache.get("global_pattern", default_pattern)
        regex = pattern_to_regex(pattern)
        match = regex.match(member.display_name)
        if match:
            try:
                return match.group("name").strip()
            except (IndexError, KeyError):
                return member.display_name
        else:
            # Fallback: Versuche es mit dem alten Pattern ohne Level
            old_pattern = "{name} [{icons}]"
            old_regex = pattern_to_regex(old_pattern)
            old_match = old_regex.match(member.display_name)
            if old_match:
                try:
                    return old_match.group("name").strip()
                except (IndexError, KeyError):
                    pass
            
            # Einfacher Fallback: Suche nach Name vor eckigen Klammern
            bracket_match = re.match(r'^(.*?)\s*\[.*\]$', member.display_name)
            if bracket_match:
                return bracket_match.group(1).strip()
                
            return member.display_name

    await spreadsheet.stats.stats(spreadsheet_acc, interaction, parse_name, spreadsheet_role_settings_manager)


@tree.command(name="set_company_role", description="Setze die Company Rolle")
@app_commands.checks.has_permissions(administrator=True)
async def set_company_role(interaction: discord.Interaction, role: discord.Role, str_in_spreadsheet: str):
    roles = await spreadsheet_role_settings_manager.load()
    if "company_role" not in roles:
        roles["company_role"] = {}

    roles["company_role"][str(role.id)] = str_in_spreadsheet
    await spreadsheet_role_settings_manager.save(roles)
    await interaction.response.send_message(f"Company Rolle wurde erfolgreich gesetzt!", ephemeral=True)

@tree.command(name="remove_company_role", description="Entferne die Company Rolle")
@app_commands.checks.has_permissions(administrator=True)
async def remove_company_role(interaction: discord.Interaction, role: discord.Role):
    roles = await spreadsheet_role_settings_manager.load()
    if "company_role" not in roles:
        roles["company_role"] = {}

    if str(role.id) in roles["company_role"]:
        del roles["company_role"][str(role.id)]
        await spreadsheet_role_settings_manager.save(roles)
        await interaction.response.send_message(f"Company Rolle wurde erfolgreich entfernt!", ephemeral=True)
    else:
        await interaction.response.send_message(f"Company Rolle nicht gefunden!", ephemeral=True)

@tree.command(name="list_company_roles", description="Liste alle Company Rollen")
@app_commands.checks.has_permissions(administrator=True)
async def list_company_roles(interaction: discord.Interaction):
    roles = await spreadsheet_role_settings_manager.load()
    if "company_role" not in roles:
        roles["company_role"] = {}

    if not roles["company_role"]:
        await interaction.response.send_message("Es sind keine Company Rollen vorhanden.", ephemeral=True)
        return

    embed = discord.Embed(
        title="Company Rollen",
        description="Konfigurierte Company Rollen:",
        color=discord.Color.blue()
    )
    for role_id, str_in_spreadsheet in roles["company_role"].items():
        role_obj = interaction.guild.get_role(int(role_id))
        role_name = role_obj.name if role_obj else f"Unbekannte Rolle ({role_id})"
        embed.add_field(name=role_name, value=f"- In Spreadsheet: {str_in_spreadsheet}", inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="set_class_role", description="Setze die Class Rolle")
@app_commands.checks.has_permissions(administrator=True)
async def set_class_role(interaction: discord.Interaction, role: discord.Role, str_in_spreadsheet: str):
    roles = await spreadsheet_role_settings_manager.load()
    if "class_role" not in roles:
        roles["class_role"] = {}

    roles["class_role"][str(role.id)] = str_in_spreadsheet
    await spreadsheet_role_settings_manager.save(roles)
    await interaction.response.send_message(f"Class Rolle wurde erfolgreich gesetzt!", ephemeral=True)

@tree.command(name="remove_class_role", description="Entferne die Class Rolle")
@app_commands.checks.has_permissions(administrator=True)
async def remove_class_role(interaction: discord.Interaction, role: discord.Role):
    roles = await spreadsheet_role_settings_manager.load()
    if "class_role" not in roles:
        roles["class_role"] = {}

    if str(role.id) in roles["class_role"]:
        del roles["class_role"][str(role.id)]
        await spreadsheet_role_settings_manager.save(roles)
        await interaction.response.send_message(f"Class Rolle wurde erfolgreich entfernt!", ephemeral=True)
    else:
        await interaction.response.send_message(f"Class Rolle nicht gefunden!", ephemeral=True)

@tree.command(name="list_class_roles", description="Liste alle Class Rollen")
@app_commands.checks.has_permissions(administrator=True)
async def list_class_roles(interaction: discord.Interaction):
    roles = await spreadsheet_role_settings_manager.load()
    if "class_role" not in roles:
        roles["class_role"] = {}

    if not roles["class_role"]:
        await interaction.response.send_message("Es sind keine Class Rollen vorhanden.", ephemeral=True)
        return

    embed = discord.Embed(
        title="Class Rollen",
        description="Konfigurierte Class Rollen:",
        color=discord.Color.blue()
    )
    for role_id, str_in_spreadsheet in roles["class_role"].items():
        role_obj = interaction.guild.get_role(int(role_id))
        role_name = role_obj.name if role_obj else f"Unbekannte Rolle ({role_id})"
        embed.add_field(name=role_name, value=f"- In Spreadsheet: {str_in_spreadsheet}", inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="set_document", description="Setze das Spreadsheet Dokument")
@app_commands.checks.has_permissions(administrator=True)
async def set_document(interaction: discord.Interaction, document_id: str):
    roles = await spreadsheet_role_settings_manager.load()
    roles["document_id"] = document_id
    await spreadsheet_role_settings_manager.save(roles)
    await interaction.response.send_message(f"Document wurde erfolgreich gesetzt!", ephemeral=True)

@tree.command(name="sort_spreadsheet", description="Sortiere das Spreadsheet")
@app_commands.checks.has_permissions(administrator=True)
async def sort_spreadsheet(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await spreadsheet.memberlist.sort_member(spreadsheet_acc, spreadsheet_role_settings_manager)
    await interaction.edit_original_response(content=f"Spreadsheet wurde erfolgreich sortiert!")

@tree.command(name="set_icon_post_channel", description="Setze den Channel für Icon Posts")
@app_commands.checks.has_permissions(administrator=True)
async def set_icon_post_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    channels = await gp_channel_manager.load()
    channels["icon_post_channel"] = channel.id
    await gp_channel_manager.save(channels)
    await interaction.response.send_message(f"Icon Post Channel wurde erfolgreich gesetzt!", ephemeral=True)

async def post_icons_to_channel(interaction: discord.Interaction):
    global role_name_update_settings_cache
    channels = await gp_channel_manager.load()
    channel_id = channels.get("icon_post_channel", None)
    if channel_id:
        channel = bot.get_channel(channel_id)

        role_settings = role_name_update_settings_cache.get("role_settings", {})
        icons_with_roles = []

        # Sammle die Daten inklusive prio, Icon und Rollennamen
        for role_id, settings in role_settings.items():
            role = interaction.guild.get_role(int(role_id))
            if role:
                icon = settings.get("icon", "")
                prio = settings.get("prio", 0)
                try:
                    prio = int(prio)
                except ValueError:
                    prio = 0
                icons_with_roles.append((prio, icon, role.name))

        # Sortiere die Liste nach prio (aufsteigend)
        icons_with_roles.sort(key=lambda x: x[0], reverse=True)

        msg = ''.join(f"[{icon}] - {role_name}\n" for prio, icon , role_name in icons_with_roles if icon)

        msg = f"## Aktuelle Symbolbedeutung!\n\n{msg}"

        # look if there is a post sent by the bot
        async for message in channel.history(limit=1):
            if message.author == bot.user:
                # format the icons like this: [icon] - name of role and next line

                await message.edit(content=f"{msg}")
                return
    
        await channel.send(f"{msg}")
        return

@tree.command(name="set_channel_raidhelper_race", description="Setze den Channel für Raidhelper Races")
@app_commands.checks.has_permissions(administrator=True)
async def set_channel_raidhelper_race(interaction: discord.Interaction, channel: discord.TextChannel):
    channels = await gp_channel_manager.load()
    channels["raidhelper_race"] = channel.id
    await gp_channel_manager.save(channels)
    await interaction.response.send_message(f"Raidhelper Channel Set for Races", ephemeral=True)

@tree.command(name="set_channel_raidhelper_war", description="Setze den Channel für Raidhelper_war")
@app_commands.checks.has_permissions(administrator=True)
async def set_channel_raidhelper_war(interaction: discord.Interaction, channel: discord.TextChannel):
    channels = await gp_channel_manager.load()
    channels["raidhelper_war"] = channel.id
    await gp_channel_manager.save(channels)
    await interaction.response.send_message(f"Raidhelper Channel Set for War", ephemeral=True)

@tree.command(name="remove_channel_raidhelper_race", description="Entferne den Channelfür Raidhelper")
@app_commands.checks.has_permissions(administrator=True)
async def remove_channel_raidhelper_race(interaction: discord.Interaction):
    channels = await gp_channel_manager.load()
    channels["raidhelper_race"] = None
    await gp_channel_manager.save(channels)
    await interaction.response.send_message(f"Raidhelper Channel Removed", ephemeral=True)

@tree.command(name="remove_channel_raidhelper_war", description="Entferne den Channelfür Raidhelper")
@app_commands.checks.has_permissions(administrator=True)
async def remove_channel_raidhelper_war(interaction: discord.Interaction):
    channels = await gp_channel_manager.load()
    channels["raidhelper_war"] = None
    await gp_channel_manager.save(channels)
    await interaction.response.send_message(f"Raidhelper Channel Removed", ephemeral=True)

@tree.command(name="abwesenheit", description="Teile uns mit wann du Abwesend bist")
async def abwesenheit(interaction: discord.Interaction):
    modal = spreadsheet.urlaub.UrlaubsModal()

    def parse_name(member : discord.Member):
        pattern = role_name_update_settings_cache.get("global_pattern", default_pattern)
        regex = pattern_to_regex(pattern)
        match = regex.match(member.display_name)
        if match:
            try:
                return match.group("name").strip()
            except (IndexError, KeyError):
                return member.display_name
        else:
            # Fallback: Versuche es mit dem alten Pattern ohne Level
            old_pattern = "{name} [{icons}]"
            old_regex = pattern_to_regex(old_pattern)
            old_match = old_regex.match(member.display_name)
            if old_match:
                try:
                    return old_match.group("name").strip()
                except (IndexError, KeyError):
                    pass
            
            # Einfacher Fallback: Suche nach Name vor eckigen Klammern
            bracket_match = re.match(r'^(.*?)\s*\[.*\]$', member.display_name)
            if bracket_match:
                return bracket_match.group(1).strip()
                
            return member.display_name
        
    modal.fake_init(spreadsheet_acc, parse_name, spreadsheet_role_settings_manager)
    await interaction.response.send_modal(modal)

@tree.command(name="set_error_log_channel", description="Setze den Channel für Error Logs")
@app_commands.checks.has_permissions(administrator=True)
async def set_error_log_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    channels = await gp_channel_manager.load()
    channels["error_log_channel"] = channel.id
    await gp_channel_manager.save(channels)
    await interaction.response.send_message(f"Error Log Channel wurde erfolgreich gesetzt!", ephemeral=True)

# Level system helper functions
def get_xp_for_level(level):
    """Calculate total XP needed for a specific level"""
    if level <= 0:
        return 0
    
    # Cap at max level
    if level > MAX_LEVEL:
        level = MAX_LEVEL
    
    # Sum all XP requirements up to this level
    total_xp = 0
    for i in range(1, level + 1):
        total_xp += get_xp_requirement(i)
    return total_xp

def get_level_progress(current_xp):
    """Calculate level and progress based on XP"""
    level = 0
    accumulated_xp = 0
    
    # Determine level based on accumulated XP
    for i in range(1, MAX_LEVEL + 1):
        level_requirement = get_xp_requirement(i)
        if current_xp >= accumulated_xp + level_requirement:
            level = i
            accumulated_xp += level_requirement
        else:
            break
            
    # If at max level, return max level with 100% progress
    if level >= MAX_LEVEL:
        return MAX_LEVEL, 100, accumulated_xp, accumulated_xp
        
    # Calculate progress to next level
    if level < MAX_LEVEL:
        next_level_req = get_xp_requirement(level + 1)
        xp_progress = current_xp - accumulated_xp
        progress = round((xp_progress / next_level_req) * 100) if next_level_req > 0 else 100
        next_level_total = accumulated_xp + next_level_req
        return level, progress, accumulated_xp, next_level_total
    else:
        return level, 100, accumulated_xp, accumulated_xp

async def get_user_level_data(user_id):
    """Get user level data from database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM user_levels WHERE user_id = ?", (str(user_id),))
    result = cursor.fetchone()
    conn.close()
    
    if result:
        return {
            "user_id": result[0],
            "username": result[1],
            "xp": result[2],
            "level": result[3],
            "message_count": result[4],
            "voice_time": result[5],
            "streak_days": result[6] if len(result) > 6 else 0,
            "streak_multiplier": result[7] if len(result) > 7 else 1.0,
            "last_active": result[8] if len(result) > 8 else None
        }
    return None

async def ensure_user_in_db(user_id, username):
    """Create user in database if not exists"""
    user_data = await get_user_level_data(user_id)
    if not user_data:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO user_levels (user_id, username, xp, level, message_count, voice_time, streak_days, streak_multiplier, last_active) VALUES (?, ?, 0, 0, 0, 0, 0, 1.0, ?)",
            (str(user_id), username, datetime.datetime.now(datetime.timezone.utc).isoformat())
        )
        conn.commit()
        conn.close()
        return True
    return False

async def add_xp(user_id, username, xp_amount, reason="unknown"):
    """Add XP to user and check for level up"""
    await ensure_user_in_db(user_id, username)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get current XP and level
    cursor.execute("SELECT xp, level, streak_multiplier FROM user_levels WHERE user_id = ?", (str(user_id),))
    result = cursor.fetchone()
    
    current_xp, current_level = result[0], result[1]
    # Apply streak multiplier if it exists
    streak_multiplier = result[2] if len(result) > 2 else 1.0
    
    # Calculate adjusted XP with multiplier
    adjusted_xp = int(xp_amount * streak_multiplier)
    
    # Add XP
    new_xp = current_xp + adjusted_xp
    
    # Calculate new level based on XP
    new_level, _, _, _ = get_level_progress(new_xp)
    
    # Update database with new XP, level and last_active timestamp
    current_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
    cursor.execute(
        "UPDATE user_levels SET xp = ?, level = ?, last_active = ? WHERE user_id = ?",
        (new_xp, new_level, current_time, str(user_id))
    )
    
    # Record XP change in history table
    cursor.execute(
        "INSERT INTO xp_history (user_id, amount, reason, timestamp) VALUES (?, ?, ?, ?)",
        (str(user_id), adjusted_xp, reason, current_time)
    )
    
    conn.commit()
    conn.close()
    
    # Return True if level up occurred
    return new_level > current_level, new_level if new_level > current_level else None

async def add_message_xp(user_id, username):
    """Add XP for sending a message"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Increment message count
    cursor.execute(
        "UPDATE user_levels SET message_count = message_count + 1 WHERE user_id = ?",
        (str(user_id),)
    )
    conn.commit()
    conn.close()
    
    # Add 1 XP per message
    xp_earned = 1
    return await add_xp(user_id, username, xp_earned, reason="message")

@tree.command(name="level", description="Zeigt dein aktuelles Level und XP an")
async def level(interaction: discord.Interaction, user: Optional[discord.Member] = None):
    """Show user level and XP"""
    target_user = user or interaction.user
    
    # Get level data
    level_data = await get_user_level_data(target_user.id)
    if not level_data:
        # Initialize user if they don't exist yet
        await ensure_user_in_db(target_user.id, target_user.display_name)
        level_data = await get_user_level_data(target_user.id)
    
    current_xp = level_data["xp"]
    
    # Calculate level and progress
    current_level, progress, _, next_level_xp = get_level_progress(current_xp)
    
    # Format voice time
    voice_time_hours = level_data["voice_time"] // 3600
    voice_time_minutes = (level_data["voice_time"] % 3600) // 60
    voice_time_str = f"{voice_time_hours}h {voice_time_minutes}m"
    
    # Streak-Informationen
    streak_days = level_data.get("streak_days", 0)
    streak_multiplier = level_data.get("streak_multiplier", 1.0)
    
    # Create embed
    embed = discord.Embed(
        title=f"🏆 Level-Status von {target_user.display_name}",
        color=discord.Color.blue()
    )
    
    embed.add_field(name="Level", value=str(current_level), inline=True)
    
    if current_level < MAX_LEVEL:
        embed.add_field(name="XP", value=f"{current_xp}/{next_level_xp}", inline=True)
        embed.add_field(name="Fortschritt", value=f"{progress}%", inline=True)
    else:
        embed.add_field(name="XP", value=f"{current_xp} (Max Level erreicht!)", inline=True)
        embed.add_field(name="Fortschritt", value="Maximales Level erreicht", inline=True)
    
    embed.add_field(name="Nachrichten", value=str(level_data["message_count"]), inline=True)
    embed.add_field(name="Sprachzeit", value=voice_time_str, inline=True)
    
    # Streak-Information hinzufügen
    if streak_days > 0:
        streak_text = f"{streak_days} Tage"
        if streak_multiplier > 1.0:
            streak_text += f" (+{int((streak_multiplier-1)*100)}% Bonus)"
        embed.add_field(name="🔥 Streak", value=streak_text, inline=True)
    
    # Add a progress bar (20 character width)
    if current_level < MAX_LEVEL:
        progress_bar_length = 20
        filled_length = int(progress_bar_length * progress / 100)
        bar = "█" * filled_length + "░" * (progress_bar_length - filled_length)
        embed.add_field(name="Fortschritt zum nächsten Level", value=f"`{bar}` {progress}%", inline=False)
    else:
        embed.add_field(name="Fortschritt", value=f"Maximales Level ({MAX_LEVEL}) erreicht! 🎉", inline=False)
    
    # Set user avatar if available
    if target_user.avatar:
        embed.set_thumbnail(url=target_user.avatar.url)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="leaderboard", description="Zeigt die Top-Spieler nach XP an")
async def leaderboard(interaction: discord.Interaction, type: str = None):
    """Show server leaderboard"""
    # Set default type if not specified
    if type is None or type == "xp":
        sort_by = "xp"
        sort_field = "xp"
        title = "⭐ Leaderboard - Top nach XP"
    elif type == "level":
        sort_by = "level"
        sort_field = "level"
        title = "🏆 Leaderboard - Top nach Level"
    elif type == "messages":
        sort_by = "messages"
        sort_field = "message_count"
        title = "💬 Leaderboard - Top nach Nachrichten"
    elif type == "voice":
        sort_by = "voice"
        sort_field = "voice_time"
        title = "🎙️ Leaderboard - Top nach Sprachzeit"
    else:
        # Default to XP if an invalid option is provided
        sort_by = "xp"
        sort_field = "xp"
        title = "⭐ Leaderboard - Top nach XP"
    
    # Get data from database
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(f"SELECT user_id, username, xp, level, message_count, voice_time FROM user_levels ORDER BY {sort_field} DESC LIMIT 10")
    top_users = cursor.fetchall()
    conn.close()
    
    if not top_users:
        await interaction.response.send_message("Noch keine Daten in der Leaderboard-Datenbank!", ephemeral=True)
        return
    
    # Create embed
    embed = discord.Embed(
        title=title,
        description="Die aktivsten Mitglieder des Servers:",
        color=discord.Color.gold()
    )
    
    # Add fields for each user
    for i, user_data in enumerate(top_users):
        user_id, username, xp, level, message_count, voice_time = user_data
        
        # Try to get member from guild for updated username
        member = interaction.guild.get_member(int(user_id))
        display_name = member.display_name if member else username
        
        # Format voice time
        voice_hours = voice_time // 3600
        voice_minutes = (voice_time % 3600) // 60
        voice_str = f"{voice_hours}h {voice_minutes}m"
        
        # Medal for top 3
        medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"{i+1}."
        
        # Value based on sort type
        if sort_by == "xp":
            value = f"{xp} XP (Level {level})"
        elif sort_by == "level":
            value = f"Level {level} ({xp} XP)"
        elif sort_by == "messages":
            value = f"{message_count} Nachrichten (Level {level})"
        else:  # voice
            value = f"{voice_str} (Level {level})"
        
        embed.add_field(
            name=f"{medal} {display_name}",
            value=value,
            inline=False
        )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@leaderboard.autocomplete('type')
async def leaderboard_autocomplete(interaction: discord.Interaction, current: str):
    types = [
        app_commands.Choice(name="Nach XP", value="xp"),
        app_commands.Choice(name="Nach Level", value="level"),
        app_commands.Choice(name="Nach Nachrichten", value="messages"),
        app_commands.Choice(name="Nach Sprachzeit", value="voice")
    ]
    
    # Filter choices based on current input
    filtered_types = [
        choice for choice in types 
        if current.lower() in choice.name.lower() or current.lower() in choice.value.lower()
    ]
    
    return filtered_types

@tree.command(name="add_xp", description="Fügt einem Nutzer XP hinzu (nur für Admins)")
@app_commands.checks.has_permissions(administrator=True)
async def add_xp_command(interaction: discord.Interaction, user: discord.Member, amount: int):
    """Admin command to add XP to a user"""
    if amount <= 0:
        await interaction.response.send_message("Der XP-Betrag muss positiv sein!", ephemeral=True)
        return
    
    # Add XP
    leveled_up, new_level = await add_xp(user.id, user.display_name, amount, reason="admin_add")
    
    # Update nickname if level up occurred
    if leveled_up:
        await update_member_nickname(user)
    
    # Create response
    embed = discord.Embed(
        title="XP hinzugefügt",
        description=f"{amount} XP wurden zu {user.mention} hinzugefügt.",
        color=discord.Color.green()
    )
    
    if leveled_up:
        embed.add_field(name="Level Up!", value=f"{user.display_name} ist jetzt Level {new_level}!")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

# Add level rewards task to periodically reward voice activity
@tasks.loop(minutes=15)
async def reward_voice_activity():
    """Periodically rewards users for voice activity without waiting for them to leave"""
    log.info("Rewarding ongoing voice activity...")
    
    # Copy dict to avoid modification during iteration
    voice_users_copy = active_voice_users.copy()
    activity_tracker_copy = voice_activity_tracker.copy()
    
    for user_id, channels in voice_users_copy.items():
        for channel_id, session_info in channels.items():
            # Check if user is in the activity tracker
            if user_id not in activity_tracker_copy or str(channel_id) not in activity_tracker_copy[user_id]:
                continue
                
            activity_data = activity_tracker_copy[user_id][str(channel_id)]
            
            # Only reward if user is not muted and can speak in the channel
            if activity_data.get("is_muted", False) or not activity_data.get("can_speak", True) or activity_data.get("is_deafened", False):
                continue
            
            # Calculate duration since last activity
            current_time = int(time.time())
            
            # Prüfe, ob session_info ein Dictionary oder ein Integer ist
            if isinstance(session_info, dict):
                last_spoke_time = activity_data.get("last_spoke", session_info.get("start_time", current_time))
            else:
                # Legacy-Format - session_info ist ein Integer (Startzeitstempel)
                last_spoke_time = activity_data.get("last_spoke", session_info)
                
            time_since_spoke = current_time - last_spoke_time
            
            # Only reward if they've shown activity in the last 60 minutes (sent a text message while in voice)
            if time_since_spoke > 3600:  # 60 minutes in seconds
                log.info(f"User {user_id} hasn't shown activity in the last 60 minutes, skipping XP reward")
                continue
                
            # Calculate duration so far since start
            if isinstance(session_info, dict):
                start_time = session_info.get("start_time", current_time)
                last_reward_time = session_info.get("last_reward_time", start_time)
            else:
                # Legacy-Format behandeln
                start_time = session_info
                last_reward_time = session_info  # Für alte Daten nehmen wir an, dass noch keine Belohnung stattfand
                
            reward_duration = current_time - last_reward_time
            
            # Only reward if they've been in channel for at least 5 minutes
            if reward_duration >= 300:  # 5 minutes in seconds
                # Award XP (3 per minute, max for 15 minutes = 45 XP)
                minutes = min(15, max(1, int(reward_duration / 60)))
                xp_earned = minutes * 3
                
                # Get user's display name
                guild_id = bot.get_channel(int(channel_id)).guild.id if bot.get_channel(int(channel_id)) else None
                if guild_id:
                    guild = bot.get_guild(guild_id)
                    if guild:
                        member = guild.get_member(int(user_id))
                        if member:
                            # Check if permissions have changed
                            channel = bot.get_channel(int(channel_id))
                            if channel:
                                can_speak = await can_user_speak_in_channel(member, channel)
                                # Update the can_speak status
                                if user_id in voice_activity_tracker and str(channel_id) in voice_activity_tracker[user_id]:
                                    voice_activity_tracker[user_id][str(channel_id)]["can_speak"] = can_speak
                                
                                # If they can't speak anymore, skip
                                if not can_speak:
                                    continue
                            
                            # Award XP without ending session
                            leveled_up, new_level = await add_xp(user_id, member.display_name, xp_earned, reason="voice_periodic")
                            
                            # Update session structure if needed
                            if not isinstance(active_voice_users[user_id][channel_id], dict):
                                # Convert legacy integer format to dict format
                                old_start_time = active_voice_users[user_id][channel_id]
                                active_voice_users[user_id][channel_id] = {
                                    "start_time": old_start_time,
                                    "was_active": True,
                                    "last_reward_time": current_time
                                }
                            else:
                                # Mark the user as active since they're receiving XP
                                active_voice_users[user_id][channel_id]["was_active"] = True
                                
                                # Update last reward time
                                active_voice_users[user_id][channel_id]["last_reward_time"] = current_time
                            
                            # If level up occurred, update nickname without notification
                            if leveled_up and member:
                                await update_member_nickname(member)
                            
                            log.info(f"Awarded {xp_earned} XP to {member.display_name} for voice activity")

@tree.command(name="reset_levels", description="Setzt alle Level zurück (nur für Admins)")
@app_commands.checks.has_permissions(administrator=True)
async def reset_levels(interaction: discord.Interaction, confirm: bool):
    """Admin command to reset all levels"""
    if not confirm:
        await interaction.response.send_message("Um alle Level zurückzusetzen, führe den Befehl mit `confirm=True` aus.", ephemeral=True)
        return
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM user_levels")
    cursor.execute("DELETE FROM voice_sessions")
    conn.commit()
    conn.close()
    
    # Clear active voice sessions
    active_voice_users.clear()
    
    await interaction.response.send_message("Alle Level wurden zurückgesetzt!", ephemeral=True)

@tree.command(name="set_level", description="Setzt das Level eines Nutzers (nur für Admins)")
@app_commands.checks.has_permissions(administrator=True)
async def set_level(interaction: discord.Interaction, user: discord.Member, level: int):
    """Admin command to set a user's level"""
    if level < 0:
        await interaction.response.send_message("Das Level muss positiv sein!", ephemeral=True)
        return
    
    # Cap level at MAX_LEVEL
    if level > MAX_LEVEL:
        level = MAX_LEVEL
        await interaction.response.send_message(f"Level auf Maximum ({MAX_LEVEL}) begrenzt. {user.display_name} wurde auf Level {MAX_LEVEL} gesetzt.", ephemeral=True)
        return
    
    # Calculate XP based on level
    xp = get_xp_for_level(level)
    
    # Ensure user exists in DB
    await ensure_user_in_db(user.id, user.display_name)
    
    # Update level and XP
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE user_levels SET level = ?, xp = ? WHERE user_id = ?",
        (level, xp, str(user.id))
    )
    conn.commit()
    conn.close()
    
    # Update nickname with new level
    await update_member_nickname(user)
    
    await interaction.response.send_message(f"{user.display_name} wurde auf Level {level} gesetzt!", ephemeral=True)

@tree.command(name="level_stats", description="Zeigt Statistiken über das Level-System")
@app_commands.checks.has_permissions(administrator=True)
async def level_stats(interaction: discord.Interaction):
    """Admin command to view level system statistics"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get total users
    cursor.execute("SELECT COUNT(*) FROM user_levels")
    total_users = cursor.fetchone()[0]
    
    # Get total XP
    cursor.execute("SELECT SUM(xp) FROM user_levels")
    total_xp = cursor.fetchone()[0] or 0
    
    # Get total messages
    cursor.execute("SELECT SUM(message_count) FROM user_levels")
    total_messages = cursor.fetchone()[0] or 0
    
    # Get total voice time
    cursor.execute("SELECT SUM(voice_time) FROM user_levels")
    total_voice_time = cursor.fetchone()[0] or 0
    
    # Get average level
    cursor.execute("SELECT AVG(level) FROM user_levels")
    avg_level = cursor.fetchone()[0] or 0
    
    # Get highest level
    cursor.execute("SELECT MAX(level), user_id FROM user_levels")
    highest_level, highest_level_user_id = cursor.fetchone() or (0, None)
    
    # Get active voice sessions
    active_sessions = sum(len(channels) for channels in active_voice_users.values())
    
    conn.close()
    
    # Format voice time
    voice_hours = total_voice_time // 3600
    voice_minutes = (total_voice_time % 3600) // 60
    
    # Create embed
    embed = discord.Embed(
        title="📊 Level-System Statistiken",
        color=discord.Color.blue()
    )
    
    embed.add_field(name="Registrierte Nutzer", value=str(total_users), inline=True)
    embed.add_field(name="Gesamt-XP", value=f"{total_xp:,}", inline=True)
    embed.add_field(name="Durchschnittslevel", value=f"{avg_level:.1f}", inline=True)
    
    embed.add_field(name="Gesamt-Nachrichten", value=f"{total_messages:,}", inline=True)
    embed.add_field(name="Gesamt-Sprachzeit", value=f"{voice_hours}h {voice_minutes}m", inline=True)
    embed.add_field(name="Aktive Sprachsitzungen", value=str(active_sessions), inline=True)
    
    if highest_level_user_id:
        member = interaction.guild.get_member(int(highest_level_user_id))
        if member:
            embed.add_field(name="Höchstes Level", value=f"{member.mention} (Level {highest_level})", inline=False)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="leaderboard_all", description="Zeigt alle Nutzer nach XP sortiert an")
async def leaderboard_all(interaction: discord.Interaction, type: str = None):
    """Show complete server leaderboard with pagination"""
    # Set default type if not specified
    if type is None or type == "xp":
        sort_by = "xp"
        sort_field = "xp"
        title = "⭐ Komplettes Leaderboard - Nach XP"
    elif type == "level":
        sort_by = "level"
        sort_field = "level"
        title = "🏆 Komplettes Leaderboard - Nach Level"
    elif type == "messages":
        sort_by = "messages"
        sort_field = "message_count"
        title = "💬 Komplettes Leaderboard - Nach Nachrichten"
    elif type == "voice":
        sort_by = "voice"
        sort_field = "voice_time"
        title = "🎙️ Komplettes Leaderboard - Nach Sprachzeit"
    else:
        # Default to XP if an invalid option is provided
        sort_by = "xp"
        sort_field = "xp"
        title = "⭐ Komplettes Leaderboard - Nach XP"
    
    # Get all users from database
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(f"SELECT user_id, username, xp, level, message_count, voice_time FROM user_levels ORDER BY {sort_field} DESC")
    all_users = cursor.fetchall()
    conn.close()
    
    if not all_users:
        await interaction.response.send_message("Noch keine Daten in der Leaderboard-Datenbank!", ephemeral=True)
        return
    
    # Create pages with 20 users each
    users_per_page = 20
    pages = []
    for i in range(0, len(all_users), users_per_page):
        page_users = all_users[i:i + users_per_page]
        embed = discord.Embed(
            title=title,
            description=f"Seite {i//users_per_page + 1} von {(len(all_users)-1)//users_per_page + 1}",
            color=discord.Color.gold()
        )
        
        # Add fields for each user on this page
        for j, user_data in enumerate(page_users):
            user_id, username, xp, level, message_count, voice_time = user_data
            
            # Try to get member from guild for updated username
            member = interaction.guild.get_member(int(user_id))
            display_name = member.display_name if member else username
            
            # Format voice time
            voice_hours = voice_time // 3600
            voice_minutes = (voice_time % 3600) // 60
            voice_str = f"{voice_hours}h {voice_minutes}m"
            
            # Value based on sort type
            if sort_by == "xp":
                value = f"{xp} XP (Level {level})"
            elif sort_by == "level":
                value = f"Level {level} ({xp} XP)"
            elif sort_by == "messages":
                value = f"{message_count} Nachrichten (Level {level})"
            else:  # voice
                value = f"{voice_str} (Level {level})"
            
            embed.add_field(
                name=f"{i+j+1}. {display_name}",
                value=value,
                inline=False
            )
        
        # Verpacke das Embed mit einer leeren Rollenliste für den Paginator
        pages.append((embed, []))
    
    # Create view with pagination buttons
    view = Paginator(pages)
    await interaction.response.send_message(embed=pages[0][0], view=view, ephemeral=True)

@leaderboard_all.autocomplete('type')
async def leaderboard_all_autocomplete(interaction: discord.Interaction, current: str):
    types = [
        app_commands.Choice(name="Nach XP", value="xp"),
        app_commands.Choice(name="Nach Level", value="level"),
        app_commands.Choice(name="Nach Nachrichten", value="messages"),
        app_commands.Choice(name="Nach Sprachzeit", value="voice")
    ]
    
    # Filter choices based on current input
    filtered_types = [
        choice for choice in types 
        if current.lower() in choice.name.lower() or current.lower() in choice.value.lower()
    ]
    
    return filtered_types


# Neue Task: Täglich Streaks aktualisieren
@tasks.loop(hours=24)
async def update_streaks():
    log.info("[STREAK] Aktualisiere Streaks für alle Nutzer...")
    
    # Aktuelle Zeit in UTC
    today = datetime.datetime.now(datetime.timezone.utc).date()
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Alle Nutzer aus der DB abrufen
    cursor.execute("SELECT user_id, streak_days, last_active FROM user_levels")
    all_users = cursor.fetchall()
    
    for user in all_users:
        user_id = user[0]
        streak_days = user[1] if user[1] is not None else 0
        last_active = user[2]
        
        if not last_active:
            continue
            
        try:
            # Konvertiere Datum aus DB
            last_active_date = datetime.datetime.fromisoformat(last_active).date()
            days_difference = (today - last_active_date).days
            
            if days_difference == 1:
                # Konsekutiver Tag - Streak erhöhen
                new_streak = streak_days + 1
                
                # Multiplikator berechnen
                multiplier = 1.0
                if new_streak >= 14:
                    multiplier = 1.5
                elif new_streak >= 7:
                    multiplier = 1.2
                elif new_streak >= 3:
                    multiplier = 1.1
                    
                cursor.execute(
                    "UPDATE user_levels SET streak_days = ?, streak_multiplier = ? WHERE user_id = ?",
                    (new_streak, multiplier, user_id)
                )
                log.info(f"[STREAK] User {user_id}: Streak auf {new_streak} Tage erhöht, Multiplikator: {multiplier}x")
            elif days_difference > 1:
                # Streak gebrochen
                cursor.execute(
                    "UPDATE user_levels SET streak_days = 0, streak_multiplier = 1.0 WHERE user_id = ?",
                    (user_id,)
                )
                log.info(f"[STREAK] User {user_id}: Streak zurückgesetzt (Inaktiv seit {days_difference} Tagen)")
        except Exception as e:
            log.error(f"[STREAK] Fehler bei der Verarbeitung von User {user_id}: {str(e)}")
    
    conn.commit()
    conn.close()
    log.info("[STREAK] Streak-Aktualisierung abgeschlossen")

# Command: Streak-Informationen anzeigen
@tree.command(name="streak", description="Zeigt deine aktuelle Aktivitäts-Streak an")
async def streak(interaction: discord.Interaction, user: Optional[discord.Member] = None):
    target_user = user or interaction.user
    user_data = await get_user_level_data(target_user.id)
    
    if not user_data:
        await ensure_user_in_db(target_user.id, target_user.display_name)
        user_data = await get_user_level_data(target_user.id)
    
    streak_days = user_data.get("streak_days", 0)
    multiplier = user_data.get("streak_multiplier", 1.0)
    
    embed = discord.Embed(
        title="🔥 Aktivitäts-Streak",
        description=f"{'Du bist' if not user else f'{target_user.display_name} ist'} seit **{streak_days}** Tagen in Folge aktiv!",
        color=discord.Color.orange()
    )
    
    if multiplier > 1.0:
        embed.add_field(
            name="Aktueller XP-Bonus", 
            value=f"+{int((multiplier-1)*100)}% ({multiplier}x)"
        )
    
    next_threshold = 3 if streak_days < 3 else 7 if streak_days < 7 else 14 if streak_days < 14 else None
    if next_threshold:
        days_to_next = next_threshold - streak_days
        next_bonus = "1.1x" if next_threshold == 3 else "1.2x" if next_threshold == 7 else "1.5x"
        embed.add_field(
            name="Nächster Bonus", 
            value=f"Noch **{days_to_next}** Tag(e) bis zum {next_bonus} Multiplikator!"
        )
    
    # Set user avatar if available
    if target_user.avatar:
        embed.set_thumbnail(url=target_user.avatar.url)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

# Command: Admin-Funktion zum Setzen einer Streak
@tree.command(name="set_streak", description="Setzt die Streak eines Nutzers (nur für Admins)")
@app_commands.checks.has_permissions(administrator=True)
async def set_streak(interaction: discord.Interaction, user: discord.Member, streak_days: int):
    if streak_days < 0:
        await interaction.response.send_message("Die Streak-Tage müssen mindestens 0 sein.", ephemeral=True)
        return
    
    # Multiplier berechnen
    multiplier = 1.0
    if streak_days >= 14:
        multiplier = 1.5
    elif streak_days >= 7:
        multiplier = 1.2
    elif streak_days >= 3:
        multiplier = 1.1
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute(
        "UPDATE user_levels SET streak_days = ?, streak_multiplier = ?, last_active = ? WHERE user_id = ?",
        (streak_days, multiplier, datetime.datetime.now(datetime.timezone.utc).isoformat(), str(user.id))
    )
    
    # Wenn der User noch nicht in der Datenbank ist, erstelle ihn
    if cursor.rowcount == 0:
        await ensure_user_in_db(user.id, user.display_name)
        cursor.execute(
            "UPDATE user_levels SET streak_days = ?, streak_multiplier = ?, last_active = ? WHERE user_id = ?",
            (streak_days, multiplier, datetime.datetime.now(datetime.timezone.utc).isoformat(), str(user.id))
        )
    
    conn.commit()
    conn.close()
    
    await interaction.response.send_message(
        f"Streak von {user.display_name} wurde auf {streak_days} Tage gesetzt "
        f"(Multiplikator: {multiplier}x).", 
        ephemeral=True
    )

# Command: Top Streak-Leaderboard anzeigen
@tree.command(name="streak_leaders", description="Zeigt die Top-Spieler nach Aktivitäts-Streak an")
async def streak_leaders(interaction: discord.Interaction):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Top 10 Spieler nach Streak sortiert abrufen
    cursor.execute(
        "SELECT user_id, username, streak_days, streak_multiplier FROM user_levels ORDER BY streak_days DESC LIMIT 10"
    )
    top_streaks = cursor.fetchall()
    conn.close()
    
    if not top_streaks:
        await interaction.response.send_message("Noch keine Streak-Daten in der Datenbank!", ephemeral=True)
        return
    
    embed = discord.Embed(
        title="🔥 Streak Leaderboard",
        description="Die aktivsten Mitglieder basierend auf täglicher Aktivität:",
        color=discord.Color.orange()
    )
    
    for i, streak_data in enumerate(top_streaks):
        user_id, username, streak_days, streak_multiplier = streak_data
        
        # Try to get member from guild for updated username
        member = interaction.guild.get_member(int(user_id))
        display_name = member.display_name if member else username
        
        # Medal for top 3
        medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"{i+1}."
        
        embed.add_field(
            name=f"{medal} {display_name}",
            value=f"{streak_days} Tage (x{streak_multiplier:.1f} Bonus)",
            inline=False
        )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="xp_history", description="Zeigt deine XP-Historie an")
async def xp_history(interaction: discord.Interaction, user: Optional[discord.Member] = None, days: int = 7):
    """Show user XP history for the specified number of days"""
    target_user = user or interaction.user
    
    if days <= 0 or days > 30:
        await interaction.response.send_message("Bitte wähle einen Zeitraum zwischen 1 und 30 Tagen.", ephemeral=True)
        return
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get XP history for the last X days
    time_threshold = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).isoformat()
    cursor.execute(
        "SELECT amount, reason, timestamp FROM xp_history WHERE user_id = ? AND timestamp > ? ORDER BY timestamp DESC LIMIT 100",
        (str(target_user.id), time_threshold)
    )
    history_entries = cursor.fetchall()
    
    conn.close()
    
    if not history_entries:
        await interaction.response.send_message(f"Keine XP-Historie für {target_user.display_name} in den letzten {days} Tagen gefunden.", ephemeral=True)
        return
    
    # Group by reason for summary
    reason_totals = {}
    daily_xp = {}
    
    for amount, reason, timestamp in history_entries:
        # Add to reason totals
        if reason not in reason_totals:
            reason_totals[reason] = 0
        reason_totals[reason] += amount
        
        # Get day part of timestamp for daily grouping
        try:
            date_obj = datetime.datetime.fromisoformat(timestamp)
            day_key = date_obj.strftime("%Y-%m-%d")
            
            if day_key not in daily_xp:
                daily_xp[day_key] = 0
            daily_xp[day_key] += amount
        except ValueError:
            # Handle timestamp parsing errors
            pass
    
    # Create embed
    embed = discord.Embed(
        title=f"📊 XP-Historie: {target_user.display_name}",
        description=f"XP-Veränderungen der letzten {days} Tage:",
        color=discord.Color.blue()
    )
    
    # Add summary by reason
    summary_text = ""
    total_xp = sum(reason_totals.values())
    
    for reason, amount in sorted(reason_totals.items(), key=lambda x: x[1], reverse=True):
        reason_display = {
            "message": "Nachrichten",
            "voice": "Sprachchat (einmalig)",
            "voice_periodic": "Sprachchat (periodisch)",
            "admin_add": "Admin-Zuweisung",
            "unknown": "Unbekannt"
        }.get(reason, reason)
        
        percent = (amount / total_xp) * 100 if total_xp > 0 else 0
        summary_text += f"**{reason_display}**: {amount} XP ({percent:.1f}%)\n"
    
    embed.add_field(name="XP nach Quelle", value=summary_text or "Keine Daten", inline=False)
    
    # Add daily summary - most recent days first
    daily_summary = ""
    for day, amount in sorted(daily_xp.items(), reverse=True)[:7]:  # Show last 7 days max
        daily_summary += f"**{day}**: {amount} XP\n"
    
    embed.add_field(name="XP nach Tag", value=daily_summary or "Keine Daten", inline=False)
    
    # Add total
    embed.add_field(name="Gesamt-XP in diesem Zeitraum", value=f"{total_xp} XP", inline=False)
    
    # Set thumbnail
    if target_user.avatar:
        embed.set_thumbnail(url=target_user.avatar.url)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="monthly_stats", description="Zeigt XP-Statistiken für einen bestimmten Monat")
async def monthly_stats(interaction: discord.Interaction, year: int = None, month: int = None):
    """Show server-wide XP statistics for a specific month"""
    # Default to current month if not specified
    now = datetime.datetime.now()
    year = year or now.year
    month = month or now.month
    
    # Validate date inputs
    if year < 2020 or year > now.year + 1:
        await interaction.response.send_message("Bitte gib ein Jahr zwischen 2020 und dem nächsten Jahr an.", ephemeral=True)
        return
    
    if month < 1 or month > 12:
        await interaction.response.send_message("Bitte gib einen Monat zwischen 1 und 12 an.", ephemeral=True)
        return
    
    # Calculate date range for the month
    start_date = datetime.datetime(year, month, 1, tzinfo=datetime.timezone.utc).isoformat()
    
    # Calculate end date (first day of next month)
    if month == 12:
        end_date = datetime.datetime(year + 1, 1, 1, tzinfo=datetime.timezone.utc).isoformat()
    else:
        end_date = datetime.datetime(year, month + 1, 1, tzinfo=datetime.timezone.utc).isoformat()
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get top users for the month
    cursor.execute(
        """
        SELECT user_id, SUM(amount) as total_xp
        FROM xp_history
        WHERE timestamp >= ? AND timestamp < ?
        GROUP BY user_id
        ORDER BY total_xp DESC
        LIMIT 10
        """,
        (start_date, end_date)
    )
    top_users = cursor.fetchall()
    
    # Get total XP for the month
    cursor.execute(
        "SELECT SUM(amount) FROM xp_history WHERE timestamp >= ? AND timestamp < ?",
        (start_date, end_date)
    )
    total_xp = cursor.fetchone()[0] or 0
    
    # Get XP by reason
    cursor.execute(
        """
        SELECT reason, SUM(amount) as reason_xp
        FROM xp_history
        WHERE timestamp >= ? AND timestamp < ?
        GROUP BY reason
        ORDER BY reason_xp DESC
        """,
        (start_date, end_date)
    )
    xp_by_reason = cursor.fetchall()
    
    # Get XP by day
    cursor.execute(
        """
        SELECT strftime('%Y-%m-%d', timestamp) as day, SUM(amount) as day_xp
        FROM xp_history
        WHERE timestamp >= ? AND timestamp < ?
        GROUP BY day
        ORDER BY day
        """,
        (start_date, end_date)
    )
    xp_by_day = cursor.fetchall()
    
    conn.close()
    
    # Create month name string
    month_names = ["Januar", "Februar", "März", "April", "Mai", "Juni", 
                   "Juli", "August", "September", "Oktober", "November", "Dezember"]
    month_name = month_names[month-1]
    
    # Create embed
    embed = discord.Embed(
        title=f"📊 Server-Statistik: {month_name} {year}",
        description=f"XP-Aktivität im Monat {month_name} {year}",
        color=discord.Color.gold()
    )
    
    # Add top users
    if top_users:
        top_users_text = ""
        for i, (user_id, xp) in enumerate(top_users):
            # Try to get member from guild for updated username
            member = interaction.guild.get_member(int(user_id))
            display_name = member.display_name if member else f"User {user_id}"
            
            medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"{i+1}."
            top_users_text += f"{medal} **{display_name}**: {xp} XP\n"
        
        embed.add_field(name=f"Top {len(top_users)} Nutzer", value=top_users_text, inline=False)
    else:
        embed.add_field(name="Top Nutzer", value="Keine Daten für diesen Monat", inline=False)
    
    # Add XP by reason
    if xp_by_reason:
        reason_text = ""
        for reason, amount in xp_by_reason:
            reason_display = {
                "message": "Nachrichten",
                "voice": "Sprachchat (einmalig)",
                "voice_periodic": "Sprachchat (periodisch)",
                "admin_add": "Admin-Zuweisung",
                "unknown": "Unbekannt"
            }.get(reason, reason)
            
            percent = (amount / total_xp) * 100 if total_xp > 0 else 0
            reason_text += f"**{reason_display}**: {amount} XP ({percent:.1f}%)\n"
        
        embed.add_field(name="XP nach Aktivität", value=reason_text, inline=False)
    
    # Add summary statistics
    embed.add_field(name="Gesamt-XP", value=f"{total_xp} XP", inline=True)
    embed.add_field(name="Aktive Tage", value=f"{len(xp_by_day)}", inline=True)
    
    # Add average per day
    if xp_by_day:
        avg_per_day = total_xp / len(xp_by_day)
        embed.add_field(name="Durchschnitt pro Tag", value=f"{avg_per_day:.1f} XP", inline=True)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@monthly_stats.autocomplete('month')
async def month_autocomplete(interaction: discord.Interaction, current: str):
    month_names = [
        "Januar", "Februar", "März", "April", "Mai", "Juni", 
        "Juli", "August", "September", "Oktober", "November", "Dezember"
    ]
    
    # Datenbankabfrage für verfügbare Monate durchführen
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Wir nutzen das Jahr aus dem aktuellen Befehlskontext, wenn verfügbar
    try:
        # Versuche, das Jahr aus den Options zu holen
        options = interaction.namespace
        selected_year = getattr(options, 'year', None)
    except:
        selected_year = None
    
    # Standardmäßig das aktuelle Jahr verwenden, wenn kein Jahr ausgewählt wurde
    if not selected_year:
        selected_year = datetime.datetime.now().year
    
    # Abfrage nach Monaten mit Daten für das ausgewählte Jahr
    cursor.execute(
        """
        SELECT DISTINCT strftime('%m', timestamp) as month 
        FROM xp_history 
        WHERE strftime('%Y', timestamp) = ? 
        ORDER BY month
        """,
        (str(selected_year),)
    )
    
    available_months = [int(row[0]) for row in cursor.fetchall()]
    conn.close()
    
    # Wenn keine Daten für das Jahr existieren, Standard-Monate des aktuellen Jahres zeigen
    if not available_months:
        # Für das aktuelle Jahr zeigen wir nur Monate bis zum aktuellen Monat
        if selected_year == datetime.datetime.now().year:
            available_months = list(range(1, datetime.datetime.now().month + 1))
        else:
            available_months = list(range(1, 13))
    
    # Erstelle Choices nur für Monate mit Daten
    months = [
        app_commands.Choice(name=f"{month_names[m-1]} {selected_year}", value=m)
        for m in available_months
    ]
    
    if not current:
        return months
    
    return [
        choice for choice in months 
        if current.lower() in choice.name.lower() or (current.isdigit() and int(current) == choice.value)
    ]

@monthly_stats.autocomplete('year')
async def year_autocomplete(interaction: discord.Interaction, current: str):
    # Datenbankabfrage für verfügbare Jahre durchführen
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute(
        """
        SELECT DISTINCT strftime('%Y', timestamp) as year 
        FROM xp_history 
        ORDER BY year DESC
        """
    )
    
    available_years = [int(row[0]) for row in cursor.fetchall()]
    conn.close()
    
    # Wenn keine Daten gefunden wurden, zeige aktuelle Jahr und letztes Jahr
    now = datetime.datetime.now()
    if not available_years:
        available_years = [now.year, now.year - 1]
    
    # Stelle sicher, dass das aktuelle Jahr immer dabei ist
    if now.year not in available_years:
        available_years.append(now.year)
        available_years.sort(reverse=True)
    
    years = [
        app_commands.Choice(name=str(year), value=year)
        for year in available_years
    ]
    
    if not current:
        return years
    
    return [
        choice for choice in years 
        if current in choice.name
    ]

@tree.command(name="xp_graph", description="Zeigt einen Graphen deiner XP-Entwicklung an")
async def xp_graph(interaction: discord.Interaction, user: Optional[discord.Member] = None, days: int = 30):
    """Generate and show a graph of XP changes over time"""
    await interaction.response.defer()  # This might take a moment
    
    target_user = user or interaction.user
    
    if days <= 0 or days > 90:
        await interaction.followup.send("Bitte wähle einen Zeitraum zwischen 1 und 90 Tagen.", ephemeral=True)
        return
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get XP history for the specified days
    time_threshold = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).isoformat()
    cursor.execute(
        """
        SELECT timestamp, amount, reason 
        FROM xp_history 
        WHERE user_id = ? AND timestamp > ? 
        ORDER BY timestamp
        """,
        (str(target_user.id), time_threshold)
    )
    history_entries = cursor.fetchall()
    
    conn.close()
    
    if not history_entries:
        await interaction.followup.send(f"Keine XP-Historie für {target_user.display_name} in den letzten {days} Tagen gefunden.", ephemeral=True)
        return
    
    # Process data for plotting
    timestamps = []
    amounts = []
    reasons = []
    
    for timestamp_str, amount, reason in history_entries:
        try:
            timestamp = datetime.datetime.fromisoformat(timestamp_str)
            timestamps.append(timestamp)
            amounts.append(amount)
            reasons.append(reason)
        except ValueError:
            # Skip entries with invalid timestamps
            continue
    
    if not timestamps:
        await interaction.followup.send("Konnte die Zeitstempel nicht korrekt verarbeiten.", ephemeral=True)
        return
    
    # Create daily aggregation
    daily_xp = {}
    
    for i in range(len(timestamps)):
        day_key = timestamps[i].date()
        if day_key not in daily_xp:
            daily_xp[day_key] = 0
        daily_xp[day_key] += amounts[i]
    
    # Sort days and prepare plot data
    plot_days = sorted(daily_xp.keys())
    plot_values = [daily_xp[day] for day in plot_days]
    
    # Create a cumulative XP line
    cumulative_xp = []
    running_total = 0
    for val in plot_values:
        running_total += val
        cumulative_xp.append(running_total)
    
    # Create the plot
    plt.figure(figsize=(12, 6))
    
    # Bar chart for daily XP
    ax1 = plt.subplot(111)
    bars = ax1.bar(plot_days, plot_values, alpha=0.6, color='skyblue', width=0.8)
    ax1.set_ylabel('Tägliche XP', color='skyblue')
    ax1.tick_params(axis='y', labelcolor='skyblue')
    
    # Format x-axis to show dates nicely
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%d.%m'))
    if len(plot_days) > 14:
        plt.xticks(rotation=45)
        # Show fewer x-axis labels when there are many days
        ax1.xaxis.set_major_locator(mdates.DayLocator(interval=max(1, len(plot_days) // 10)))
    
    # Add cumulative line on secondary y-axis
    ax2 = ax1.twinx()
    ax2.plot(plot_days, cumulative_xp, color='orange', marker='o', linestyle='-', linewidth=2, markersize=4)
    ax2.set_ylabel('Kumulative XP', color='orange')
    ax2.tick_params(axis='y', labelcolor='orange')
    
    # Set titles and labels
    plt.title(f'XP-Entwicklung für {target_user.display_name} (letzte {days} Tage)')
    plt.grid(True, linestyle='--', alpha=0.6)
    
    # Ensure layout fits well
    plt.tight_layout()
    
    # Save plot to a buffer
    buffer = io.BytesIO()
    plt.savefig(buffer, format='png', dpi=100)
    buffer.seek(0)
    plt.close()
    
    # Calculate statistics
    total_xp = sum(plot_values)
    avg_daily_xp = total_xp / len(plot_days) if plot_days else 0
    max_day = max(plot_days, key=lambda day: daily_xp[day]) if plot_days else None
    max_value = daily_xp[max_day] if max_day else 0
    
    # Create description with statistics
    description = (
        f"**Zeitraum:** {plot_days[0].strftime('%d.%m.%Y')} bis {plot_days[-1].strftime('%d.%m.%Y')}\n"
        f"**Gesamt-XP:** {total_xp} XP\n"
        f"**Durchschnitt:** {avg_daily_xp:.1f} XP pro Tag\n"
        f"**Höchster Tag:** {max_day.strftime('%d.%m.%Y')} mit {max_value} XP"
    )
    
    # Create file and send
    file = discord.File(buffer, filename="xp_graph.png")
    embed = discord.Embed(
        title=f"📈 XP-Grafik für {target_user.display_name}",
        description=description,
        color=discord.Color.blue()
    )
    embed.set_image(url="attachment://xp_graph.png")
    
    await interaction.followup.send(embed=embed, file=file, ephemeral=True)

@tree.command(name="server_activity", description="Zeigt die Server-Aktivität als Grafik an")
@app_commands.checks.has_permissions(administrator=True)
async def server_activity(interaction: discord.Interaction, days: int = 30):
    """Generate and show a graph of server-wide XP activity"""
    await interaction.response.defer()  # This might take a moment
    
    if days <= 0 or days > 90:
        await interaction.followup.send("Bitte wähle einen Zeitraum zwischen 1 und 90 Tagen.", ephemeral=True)
        return
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get XP history for all users in the specified time range
    time_threshold = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).isoformat()
    cursor.execute(
        """
        SELECT timestamp, SUM(amount) as daily_xp, reason
        FROM xp_history 
        WHERE timestamp > ?
        GROUP BY strftime('%Y-%m-%d', timestamp), reason
        ORDER BY timestamp
        """,
        (time_threshold,)
    )
    history_entries = cursor.fetchall()
    
    conn.close()
    
    if not history_entries:
        await interaction.followup.send(f"Keine XP-Daten für die letzten {days} Tage gefunden.", ephemeral=True)
        return
    
    # Group data by day and reason
    daily_data = {}
    
    for timestamp_str, amount, reason in history_entries:
        try:
            timestamp = datetime.datetime.fromisoformat(timestamp_str)
            day_key = timestamp.date()
            
            if day_key not in daily_data:
                daily_data[day_key] = {'total': 0, 'voice': 0, 'message': 0, 'admin': 0, 'other': 0}
            
            daily_data[day_key]['total'] += amount
            
            # Group reasons
            if 'voice' in reason:
                daily_data[day_key]['voice'] += amount
            elif reason == 'message':
                daily_data[day_key]['message'] += amount
            elif reason == 'admin_add':
                daily_data[day_key]['admin'] += amount
            else:
                daily_data[day_key]['other'] += amount
                
        except ValueError:
            # Skip entries with invalid timestamps
            continue
    
    # Sort days
    sorted_days = sorted(daily_data.keys())
    
    if not sorted_days:
        await interaction.followup.send("Konnte die Daten nicht korrekt verarbeiten.", ephemeral=True)
        return
    
    # Prepare plot data
    plot_days = sorted_days
    total_values = [daily_data[day]['total'] for day in plot_days]
    voice_values = [daily_data[day]['voice'] for day in plot_days]
    message_values = [daily_data[day]['message'] for day in plot_days]
    admin_values = [daily_data[day]['admin'] for day in plot_days]
    other_values = [daily_data[day]['other'] for day in plot_days]
    
    # Create the stacked bar plot
    plt.figure(figsize=(14, 8))
    
    # Set font properties - only use DejaVu Sans which is available
    plt.rcParams['font.family'] = ['DejaVu Sans']
    
    # Plot stacked bars
    plt.bar(plot_days, message_values, color='#3498db', label='Nachrichten')
    plt.bar(plot_days, voice_values, bottom=message_values, color='#2ecc71', label='Sprachchat')
    
    # Calculate position for admin and other
    bottom_values = []
    for i in range(len(plot_days)):
        bottom_values.append(message_values[i] + voice_values[i])
    
    plt.bar(plot_days, admin_values, bottom=bottom_values, color='#e74c3c', label='Admin')
    
    # Calculate new bottom for other
    for i in range(len(plot_days)):
        bottom_values[i] += admin_values[i]
    
    plt.bar(plot_days, other_values, bottom=bottom_values, color='#95a5a6', label='Andere')
    
    # Format x-axis to show dates nicely
    plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%d.%m'))
    if len(plot_days) > 14:
        plt.xticks(rotation=45)
        # Show fewer x-axis labels when there are many days
        plt.gca().xaxis.set_major_locator(mdates.DayLocator(interval=max(1, len(plot_days) // 10)))
    
    # Set titles and labels
    plt.title(f'Server-Aktivität der letzten {days} Tage')
    plt.xlabel('Datum')
    plt.ylabel('XP')
    plt.legend()
    plt.grid(True, linestyle='--', alpha=0.3)
    
    # Ensure layout fits well
    plt.tight_layout()
    
    # Save plot to a buffer
    buffer = io.BytesIO()
    plt.savefig(buffer, format='png', dpi=100)
    buffer.seek(0)
    plt.close()
    
    # Calculate statistics
    total_xp = sum(total_values)
    active_days = len(plot_days)
    avg_per_day = total_xp / active_days if active_days > 0 else 0
    max_day = max(plot_days, key=lambda day: daily_data[day]['total']) if plot_days else None
    max_value = daily_data[max_day]['total'] if max_day else 0
    
    # Calculate percentage by type
    total_voice = sum(voice_values)
    total_message = sum(message_values)
    total_admin = sum(admin_values)
    total_other = sum(other_values)
    
    voice_percent = (total_voice / total_xp * 100) if total_xp > 0 else 0
    message_percent = (total_message / total_xp * 100) if total_xp > 0 else 0
    admin_percent = (total_admin / total_xp * 100) if total_xp > 0 else 0
    other_percent = (total_other / total_xp * 100) if total_xp > 0 else 0
    
    # Create description with statistics
    description = (
        f"**Zeitraum:** {plot_days[0].strftime('%d.%m.%Y')} bis {plot_days[-1].strftime('%d.%m.%Y')}\n"
        f"**Gesamt-XP:** {total_xp:,} XP über {active_days} Tage\n"
        f"**Durchschnitt:** {avg_per_day:.1f} XP pro Tag\n"
        f"**Höchster Tag:** {max_day.strftime('%d.%m.%Y')} mit {max_value:,} XP\n\n"
        f"**Verteilung:**\n"
        f"🎙️ Sprachchat: {total_voice:,} XP ({voice_percent:.1f}%)\n"
        f"💬 Nachrichten: {total_message:,} XP ({message_percent:.1f}%)\n"
        f"👑 Admin: {total_admin:,} XP ({admin_percent:.1f}%)\n"
        f"🔍 Andere: {total_other:,} XP ({other_percent:.1f}%)"
    )
    
    # Create file and send
    file = discord.File(buffer, filename="server_activity.png")
    embed = discord.Embed(
        title="📊 Server-Aktivitäts-Analyse",
        description=description,
        color=discord.Color.gold()
    )
    embed.set_image(url="attachment://server_activity.png")
    
    await interaction.followup.send(embed=embed, file=file, ephemeral=False)

bot.run(DISCORD_TOKEN)
