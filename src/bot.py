import discord
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
from videoAnalyzer import VideoAnalyzer
from collections import deque
from google import genai
from logger import logger as log
import matplotlib

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
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

stamina_lock = asyncio.Lock()
stamina_queue = deque()

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

# Datei, in der die Kanäle gespeichert werden
VOD_CHANNELS_FILE_PATH = "./vod_channels.json"
vod_channels_file_lock = asyncio.Lock()

async def load_channels():
    async with vod_channels_file_lock:
        if not os.path.exists(VOD_CHANNELS_FILE_PATH):
            return {}
        with open(VOD_CHANNELS_FILE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Sicherstellen, dass jeder Channel das `hidden`-Attribut hat
        for channel_id, info in data.items():
            if "hidden" not in info:
                info["hidden"] = False
        return data

async def save_channels(channels):
    # async with vod_channels_file_lock:
        with open(VOD_CHANNELS_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(channels, f, indent=4)

@tree.command(name="add_this_channel", description="Füge diesen Channel zur VOD-Prüfliste hinzu")
async def add_this_channel(interaction: discord.Interaction, hidden: bool = False):
    channels = await load_channels()
    channel_id = str(interaction.channel.id)

    if channel_id in channels:
        await interaction.response.send_message("Dieser Channel ist bereits in der VOD-Prüfliste.", ephemeral=True)
        return

    channels[channel_id] = {"hidden": hidden}
    await save_channels(channels)
    await interaction.response.send_message(
        f"Channel wurde erfolgreich zur VOD-Prüfliste hinzugefügt! (Hidden: {hidden})",
        ephemeral=True
    )

@tree.command(name="remove_this_channel", description="Entferne diesen Channel von der VOD-Prüfliste")
async def remove_this_channel(interaction: discord.Interaction):
    channels = await load_channels()
    channel_id = str(interaction.channel.id)

    if channel_id not in channels:
        await interaction.response.send_message("Dieser Channel ist nicht in der VOD-Prüfliste.", ephemeral=True)
        return

    del channels[channel_id]
    await save_channels(channels)
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
    await tree.sync()
    log.info(f"✅ {bot.user} ist online und bereit!")

# YouTube-Link-Erkennung
YOUTUBE_REGEX = re.compile(r"(https?://)?(www\.)?(youtube\.com|youtu\.?be)/.+")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    
    channels = await load_channels()
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

bot.run(DISCORD_TOKEN)
