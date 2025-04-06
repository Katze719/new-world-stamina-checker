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

matplotlib.use('Agg')  # Nutzt ein nicht-interaktives Backend für Speicherung
import matplotlib.pyplot as plt


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
        voice_time INTEGER DEFAULT 0
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
    
    conn.commit()
    conn.close()

# Initialize database
init_level_db()

# Store active voice users {user_id: {channel_id: start_time}}
active_voice_users = {}

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
    
    # Notify user of level up in DM
    if leveled_up:
        try:
            level_up_embed = discord.Embed(
                title="🎉 Level Up!",
                description=f"Congratulations! You've reached **Level {new_level}**!",
                color=discord.Color.gold()
            )
            await message.author.send(embed=level_up_embed)
            
            # Update nickname with new level
            await update_member_nickname(message.author)
        except discord.Forbidden:
            # User has DMs closed, notify in channel
            level_up_embed = discord.Embed(
                title="🎉 Level Up!",
                description=f"Congratulations {message.author.mention}! You've reached **Level {new_level}**!",
                color=discord.Color.gold()
            )
            await message.channel.send(embed=level_up_embed, delete_after=10)
    
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
        await start_voice_session(member.id, after.channel.id)
    
    # User left a voice channel
    elif before.channel is not None and after.channel is None:
        leveled_up, new_level = await end_voice_session(member.id, before.channel.id, member.display_name)
        
        # Notify about level up
        if leveled_up:
            try:
                level_up_embed = discord.Embed(
                    title="🎉 Level Up!",
                    description=f"Congratulations! You've reached **Level {new_level}**!",
                    color=discord.Color.gold()
                )
                await member.send(embed=level_up_embed)
            except discord.Forbidden:
                pass  # User has DMs closed
    
    # User switched voice channels
    elif before.channel is not None and after.channel is not None and before.channel.id != after.channel.id:
        # End previous session
        await end_voice_session(member.id, before.channel.id, member.display_name)
        # Start new session
        await start_voice_session(member.id, after.channel.id)
    
    # User muted/unmuted
    elif before.channel is not None and after.channel is not None and before.self_mute != after.self_mute:
        # We could add additional tracking here for when users mute/unmute
        # For now, we'll keep tracking as long as they're in the channel
        pass

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
    if len(latest_changelog) > 2048:
        latest_changelog = latest_changelog[:2045] + "..."
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

    # def parse_name(member : discord.Member):
    #     pattern = role_name_update_settings_cache.get("global_pattern", default_pattern)
    #     regex = pattern_to_regex(pattern)
    #     match = regex.match(member.display_name)
    #     if match:
    #         return match.group("name").strip()
    #     else:
    #         return member.display_name

    # await spreadsheet.payoutlist.update_payoutlist(bot, spreadsheet_acc, parse_name, spreadsheet_role_settings_manager, gp_channel_manager)
    await interaction.edit_original_response(content="Nutzer wurden Aktualisiert!")

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
            "voice_time": result[5]
        }
    return None

async def ensure_user_in_db(user_id, username):
    """Create user in database if not exists"""
    user_data = await get_user_level_data(user_id)
    if not user_data:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO user_levels (user_id, username, xp, level, message_count, voice_time) VALUES (?, ?, 0, 0, 0, 0)",
            (str(user_id), username)
        )
        conn.commit()
        conn.close()
        return True
    return False

async def add_xp(user_id, username, xp_amount):
    """Add XP to user and check for level up"""
    await ensure_user_in_db(user_id, username)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get current XP and level
    cursor.execute("SELECT xp, level FROM user_levels WHERE user_id = ?", (str(user_id),))
    current_xp, current_level = cursor.fetchone()
    
    # Add XP
    new_xp = current_xp + xp_amount
    
    # Calculate new level based on XP
    new_level, _, _, _ = get_level_progress(new_xp)
    
    # Update database
    cursor.execute(
        "UPDATE user_levels SET xp = ?, level = ? WHERE user_id = ?",
        (new_xp, new_level, str(user_id))
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
    return await add_xp(user_id, username, xp_earned)

async def start_voice_session(user_id, channel_id):
    """Record start of voice activity"""
    # Store in memory for quick access
    if user_id not in active_voice_users:
        active_voice_users[user_id] = {}
    
    # Only start if not already in this channel
    if channel_id not in active_voice_users[user_id]:
        start_time = int(time.time())
        active_voice_users[user_id][channel_id] = start_time
        
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
    """End voice session and award XP"""
    # Check if session exists
    if user_id in active_voice_users and channel_id in active_voice_users[user_id]:
        start_time = active_voice_users[user_id][channel_id]
        end_time = int(time.time())
        duration = end_time - start_time
        
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
        
        # Award XP (3 XP per minute with a minimum of 1)
        minutes = max(1, int(duration / 60))
        xp_earned = minutes * 3
        leveled_up, new_level = await add_xp(user_id, username, xp_earned)
        
        # If level up occurred, update nickname
        if leveled_up:
            # Find the guild and member object
            channel = bot.get_channel(int(channel_id))
            if channel and channel.guild:
                member = channel.guild.get_member(int(user_id))
                if member:
                    await update_member_nickname(member)
        
        return leveled_up, new_level
    
    return False, None

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
    
    await interaction.response.send_message(embed=embed)

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
        await interaction.response.send_message("Noch keine Daten in der Leaderboard-Datenbank!")
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
    
    await interaction.response.send_message(embed=embed)

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
    leveled_up, new_level = await add_xp(user.id, user.display_name, amount)
    
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
    
    await interaction.response.send_message(embed=embed)
    
    # Notify user
    try:
        user_embed = discord.Embed(
            title="XP erhalten!",
            description=f"Du hast {amount} XP von einem Administrator erhalten!",
            color=discord.Color.blue()
        )
        if leveled_up:
            user_embed.add_field(name="Level Up!", value=f"Du bist jetzt Level {new_level}!")
        await user.send(embed=user_embed)
    except discord.Forbidden:
        pass  # User has DMs closed

# Add level rewards task to periodically reward voice activity
@tasks.loop(minutes=15)
async def reward_voice_activity():
    """Periodically rewards users for voice activity without waiting for them to leave"""
    log.info("Rewarding ongoing voice activity...")
    
    # Copy dict to avoid modification during iteration
    voice_users_copy = active_voice_users.copy()
    
    for user_id, channels in voice_users_copy.items():
        for channel_id, start_time in channels.items():
            # Calculate duration so far
            current_time = int(time.time())
            duration = current_time - start_time
            
            # Only reward if they've been in channel for at least 5 minutes
            if duration >= 300:  # 5 minutes in seconds
                # Award XP (3 per minute, max for 15 minutes = 45 XP)
                minutes = min(15, max(1, int(duration / 60)))
                xp_earned = minutes * 3
                
                # Get user's display name
                guild_id = bot.get_channel(int(channel_id)).guild.id if bot.get_channel(int(channel_id)) else None
                if guild_id:
                    guild = bot.get_guild(guild_id)
                    if guild:
                        member = guild.get_member(int(user_id))
                        if member:
                            # Award XP without ending session
                            leveled_up, new_level = await add_xp(user_id, member.display_name, xp_earned)
                            
                            # Update voice time in database
                            conn = sqlite3.connect(DB_PATH)
                            cursor = conn.cursor()
                            cursor.execute(
                                "UPDATE user_levels SET voice_time = voice_time + ? WHERE user_id = ?",
                                (duration, str(user_id))
                            )
                            conn.commit()
                            conn.close()
                            
                            # Reset timer by updating start time
                            active_voice_users[user_id][channel_id] = current_time
                            
                            # Notify level up and update nickname
                            if leveled_up and member:
                                try:
                                    level_up_embed = discord.Embed(
                                        title="🎉 Level Up!",
                                        description=f"Congratulations! You've reached **Level {new_level}**!",
                                        color=discord.Color.gold()
                                    )
                                    await member.send(embed=level_up_embed)
                                    
                                    # Update nickname with new level
                                    await update_member_nickname(member)
                                except discord.Forbidden:
                                    pass  # User has DMs closed

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
    
    await interaction.response.send_message(embed=embed)

bot.run(DISCORD_TOKEN)
