import gspread.utils
import gspread_asyncio
import gspread
import discord
from enum import Enum

import jsonFileManager
import asyncio
import datetime
import locale
import re
import requests
import json
import traceback
import io
from logger import log

EDITOR_MAILS = ["bot-300@black-beach-453214-f6.iam.gserviceaccount.com", "diekrankenpfleger@gmail.com", "cyradiss1986@gmail.com", "pauldorn1234@gmail.com"]

# Setze die Locale auf Deutsch
locale.setlocale(locale.LC_TIME, "de_DE.UTF-8")

spreadsheet_payout_update = asyncio.Lock()

class Column(Enum):
    COMPANY = 'E'
    NAME = 'F'
    START = 'J'

# Offset for the first 6 entries
COLUMN_START_OFFSET = 6
ROW_START_OFFSET = 9


raidhelper_id_manager = jsonFileManager.JsonFileManager("./raidhelper_id.json")

EVENTS_FILE_PATH = "./scheduled_events.json"
events_manager = jsonFileManager.JsonFileManager(EVENTS_FILE_PATH)

def find_free_cell_in_column(column: list):
    for i, cell in enumerate(column):
        if cell == "":
            return i + 1 + COLUMN_START_OFFSET
    return len(column) + 1 + COLUMN_START_OFFSET

def find_free_cell_in_row(row: list):
    for i, cell in enumerate(row):
        if cell == "":
            return i + 1 + ROW_START_OFFSET
    return len(row) + 1 + ROW_START_OFFSET

async def extract_raidhelper_data(message, event_type="Event"):
    """Extract data from a Raidhelper message embed"""
    if not message.embeds:
        return None, None
        
    embed = message.embeds[0]
    if not embed.fields:
        return None, None
        
    embed_field_value = embed.fields[0].value
    match = re.search(r"<t:(\d+):", embed_field_value)
    if not match:
        return None, None
        
    timestamp = int(match.group(1))
    # Convert timestamp to datetime (UTC)
    dt = datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc)
    now = datetime.datetime.now(tz=datetime.timezone.utc)
    
    if now <= dt:
        # Event hasn't happened yet
        return None, None
        
    # Extract usernames from Raidhelper API
    raidhelper_usernames = []
    for field in embed.fields:
        match = re.search(r'\[Web View\]\((https://raid-helper\.dev/event/(\d+))\)', field.value)
        if match:
            event_id = match.group(2)
            api_url = f"https://raid-helper.dev/api/v2/events/{event_id}"
            response = requests.get(api_url)
            json_data = response.json()
            for signup in json_data["signUps"]:
                raidhelper_usernames.append(signup["name"])
    
    return raidhelper_usernames, dt

async def process_raidhelper_for_payoutlist(sheet, discord_member_list, discord_member_list_orig_names, raidhelper_usernames, event_time, event_type):
    """Process extracted Raidhelper data and update the payoutlist"""
    if not raidhelper_usernames:
        return False
    
    # Events-Logik für Abwesenheit laden
    events_data = await events_manager.load() or {"events": []}
    events = events_data["events"]
    
    # Filter für Abwesenheits-Events
    end_events = [event for event in events if event.get("type") == "remove_absence_indicator"]
    start_events = [event for event in events if event.get("type") == "start_absence_indicator"]
    
    # Mappe Start-Events für schnellen Zugriff
    start_lookup = {}
    for event in start_events:
        context = event.get("context", {})
        user_id = context.get("user_id")
        channel_id = context.get("channel_id")
        if user_id and channel_id:
            start_lookup[(str(user_id), str(channel_id))] = event.get("execution_date")
    
    # Baue Abwesenheits-Map: user_id -> (start, end)
    absences = {}
    for event in end_events:
        try:
            context = event.get("context", {})
            execution_date_str = event.get("execution_date", "")
            user_id = context.get("user_id")
            channel_id = context.get("channel_id")
            if not (user_id and channel_id and execution_date_str):
                continue
            end_date = datetime.datetime.fromisoformat(execution_date_str)
            start_date_str = start_lookup.get((str(user_id), str(channel_id)), None)
            if not start_date_str:
                # PATCH: Wenn kein Start-Event existiert, setze Startdatum auf 1970-01-01
                start_date = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
            else:
                start_date = datetime.datetime.fromisoformat(start_date_str)
            absences[user_id] = (start_date, end_date)
        except Exception as e:
            pass
    
    # Get next free column for event
    events_row = await sheet.get_values("J3:AAA3", major_dimension="ROWS")
    events_row = events_row[0]
    column_event = find_free_cell_in_row(events_row)
    
    # Add event columns
    await sheet.update_cell(3, column_event, "Raidhelper")
    await sheet.update_cell(4, column_event, f"{event_type} {event_time.strftime('%d.%m.')}")
    
    # Get the column A with member names
    A_col = await sheet.get_values("F1:F300", major_dimension="COLUMNS")
    A_col = A_col[0]
    A_col = A_col[COLUMN_START_OFFSET:]
    
    # Mark attendees in the payoutlist
    for i, member in enumerate(discord_member_list):
        discord_username, company = member
        discord_username_origin = discord_member_list_orig_names[i][0]
        row = find_free_cell_in_column(A_col)
        
        if any(discord_username in username for username in raidhelper_usernames):
            # Prüfe, ob Nutzer abwesend ist
            # Suche nach Discord-ID (user_id) anhand des Namens
            # Annahme: discord_username_origin ist eindeutig und entspricht dem Namen in der Abwesenheits-Map
            # Wir brauchen aber die user_id, daher muss discord_member_list_orig_names[i] um user_id erweitert werden, falls nicht vorhanden
            # Alternativ: Wir prüfen auf Basis des Namens, ob ein user_id in absences existiert, dessen Name übereinstimmt
            # Besser: Wir geben die user_id mit in discord_member_list_orig_names (muss an Aufrufer angepasst werden)
            # Hier: Wir versuchen, die user_id aus discord_username_origin zu extrahieren, falls möglich, sonst kein x
            user_id = None
            if len(discord_member_list_orig_names[i]) > 1 and isinstance(discord_member_list_orig_names[i][1], str) and discord_member_list_orig_names[i][1].isdigit():
                user_id = discord_member_list_orig_names[i][1]
            # Fallback: Kein user_id, kein x
            is_absent = False
            if user_id and user_id in absences:
                start_date, end_date = absences[user_id]
                if start_date <= event_time < end_date:
                    is_absent = True
            
            value = "x" if is_absent else "1"
            if discord_username_origin in A_col:
                row = A_col.index(discord_username_origin)
                await sheet.update_cell(row + 1 + COLUMN_START_OFFSET, column_event, value)
            else:  
                await sheet.update_cell(row, column_event, value)
                
        if discord_username_origin not in A_col:
            name_update = []
            name_update.append({
                'range': f"{Column.NAME.value}{row}",
                'values': [[discord_username_origin]]
            })
            await sheet.batch_update(name_update, value_input_option=gspread.utils.ValueInputOption.raw)
            # stats
            await sheet.update_cell(row, 1, f'=(ZÄHLENWENNS($J$4:$AAA$4; "*Push*";$J$3:$AAA$3;"*Teilnehmer*";J{row}:AAA{row}; ">0") + ZÄHLENWENNS($J$4:$AAA$4; "*Push*";$J$3:$AAA$3;"*Teilnehmer*";J{row}:AAA{row}; "x"))/$A$5')
            await sheet.update_cell(row, 2, f'=(ZÄHLENWENNS($J$4:$AAA$4; "*Krieg*";$J$3:$AAA$3;"*Teilnehmer*";J{row}:AAA{row}; ">0") + ZÄHLENWENNS($J$4:$AAA$4; "*Krieg*";$J$3:$AAA$3;"*Teilnehmer*";J{row}:AAA{row}; "x"))/$B$5')
            await sheet.update_cell(row, 3, f'=(ZÄHLENWENNS($J$3:$AAA$3;"*Raidhelper*";J{row}:AAA{row}; ">0") + ZÄHLENWENNS($J$3:$AAA$3;"*Raidhelper*";J{row}:AAA{row}; "x"))/$C$5')
            await sheet.update_cell(row, 4, f'=(ZÄHLENWENNS($J$3:$AAA$3;"*VOD*";J{row}:AAA{row}; ">0") + ZÄHLENWENNS($J$3:$AAA$3;"*VOD*";J{row}:AAA{row}; "x"))/$D$5')
            await sheet.update_cell(row, 5, company)
            await sheet.update_cell(row, 7, f'=SUMME(I{row}:AAA{row})')
            await sheet.update_cell(row, 8, f'=G{row}*$H$3')
            
            if row - 1 - COLUMN_START_OFFSET < len(A_col):
                A_col[row - 1 - COLUMN_START_OFFSET] = discord_username_origin
            else:
                A_col.append(discord_username_origin)
    
    return True

async def _update_payoutlist(bot: discord.Client, client: gspread_asyncio.AsyncioGspreadClientManager, parse_display_name: callable, spread_settings: jsonFileManager.JsonFileManager, gp_channel_manager: jsonFileManager.JsonFileManager):
    """
    Update the payout list
    """
    # Open the worksheet
    auth = await client.authorize()
    spreadsheet_role_settings = await spread_settings.load()
    worksheet = await auth.open(spreadsheet_role_settings["document_id"])
    
    channels = await gp_channel_manager.load()
    channel_race = bot.get_channel(channels.get("raidhelper_race"))
    channel_war = bot.get_channel(channels.get("raidhelper_war"))

    if not channel_race or not channel_war:
        return

    # get current month name in german
    now = datetime.datetime.now()

    # Formatieren: Monat und Jahr
    current_month = now.strftime("%B")  # Vollständiger Monatsname
    current_year = now.strftime("%Y")   # Jahr
    
    def get_company(member: discord.Member):
        user_roles_ = member.roles
        user_role_ids_ = [role.id for role in user_roles_]
        company = None
            
        for user_role_id in user_role_ids_:
            if str(user_role_id) in spreadsheet_role_settings["company_role"]:
                company = spreadsheet_role_settings["company_role"][str(user_role_id)]
                break

        return company

    def full_parse(member):
        # Parse the display name
        member_name = parse_display_name(member)

        # Mach aus namen wie "Dirty Torty | Jan" -> "Dirty Tory"
        member_name = member_name.split(" | ")[0]
        member_name = member_name.split(" I ")[0]
        member_name = member_name.replace("🏮 ", "")
        member_name = member_name.replace("🏮", "")
        member_name = member_name.replace(" ", "")
        return member_name
    
    def full_parse_specced(member):
        # Parse the display name
        member_name = parse_display_name(member)

        # Mach aus namen wie "Dirty Torty | Jan" -> "Dirty Tory"
        member_name = member_name.split(" | ")[0]
        member_name = member_name.split(" I ")[0]
        member_name = member_name.replace("🏮 ", "")
        member_name = member_name.replace("🏮", "")
        return member_name

    # check if users from sheet are in in discord
    discord_memeber_list = [(full_parse(member), get_company(member)) for member in channel_race.guild.members if get_company(member) is not None]
    discord_memeber_list_orig_names = [(full_parse_specced(member), get_company(member)) for member in channel_race.guild.members if get_company(member) is not None]

    # check if the worksheet exists if not copy the one from the last month
    try:
        sheet = await worksheet.worksheet(f"Payoutliste {current_month} {current_year}")
    except gspread.exceptions.WorksheetNotFound:
        # Copy the last month sheet
        last_month = now - datetime.timedelta(days=now.day)
        last_month_name = last_month.strftime("%B")
        last_month_year = last_month.strftime("%Y")
        last_month_sheet = await worksheet.worksheet(f"Payoutliste {last_month_name} {last_month_year}")
        await cleanup_payoutlist(last_month_sheet, discord_memeber_list_orig_names)
        await worksheet.duplicate_sheet(last_month_sheet.id, insert_sheet_index=3, new_sheet_name=f"Payoutliste {current_month} {current_year}")
        await last_month_sheet.update_tab_color("#ffffff")

        # Get the new sheet
        sheet = await worksheet.worksheet(f"Payoutliste {current_month} {current_year}")
        await sheet.add_protected_range("A1:AAA3000", EDITOR_MAILS)
        await sheet.batch_clear([f"{Column.START.value}7:AAA300", "J2:AAA4"])

        # protect new created sheet so only specific users can edit the sheet

        # Get the values
        A_col = await sheet.get_values("F1:F300", major_dimension="COLUMNS")
        A_col = A_col[0]
        A_col = A_col[COLUMN_START_OFFSET:]

        for member in A_col:
            if member.replace(" ", "") not in [mem[0] for mem in discord_memeber_list]:
                # get the row of the member
                row = A_col.index(member) + 1 + COLUMN_START_OFFSET
                await sheet.batch_clear([f"A{row}:AAA{row}"])

    # Check for new members with company roles and add them to the payoutlist
    await add_new_members_to_payoutlist(sheet, discord_memeber_list_orig_names)
    
    data = await raidhelper_id_manager.load()

    mesage_ids = data.get("raidhelper_message_ids")
    if not mesage_ids:
        data["raidhelper_message_ids"] = []

    for channel, type in [(channel_race, "Push"), (channel_war, "Krieg")]:
        async for message in channel.history(limit=100):
            if message.id in data["raidhelper_message_ids"]:
                continue
                
            raidhelper_usernames, dt = await extract_raidhelper_data(message)
            
            if raidhelper_usernames and dt:
                data["raidhelper_message_ids"].append(message.id)
                log.info("Updating payout list")
                
                await process_raidhelper_for_payoutlist(sheet, discord_memeber_list, discord_memeber_list_orig_names, 
                                                      raidhelper_usernames, dt, type)
                
                log.info("Payout list updated")
    
    await raidhelper_id_manager.save(data)

async def process_raidhelper_by_message_id(bot: discord.Client, client: gspread_asyncio.AsyncioGspreadClientManager, message_id: int, event_type: str, parse_display_name: callable, spread_settings: jsonFileManager.JsonFileManager):
    """Process a specific Raidhelper message by its ID from any channel"""
    try:
        # Find the message in any accessible channel
        message = None
        for guild in bot.guilds:
            if message:
                break
            for channel in guild.text_channels:
                try:
                    message = await channel.fetch_message(message_id)
                    if message:
                        log.info(f"Found message {message_id} in channel {channel.name}")
                        break
                except (discord.NotFound, discord.Forbidden):
                    continue
        
        if not message:
            log.error(f"Message with ID {message_id} not found in any channel")
            return False, "Message not found in any accessible channel"
        
        # Open the worksheet
        auth = await client.authorize()
        spreadsheet_role_settings = await spread_settings.load()
        worksheet = await auth.open(spreadsheet_role_settings["document_id"])
        
        # Get current month name in german
        now = datetime.datetime.now()
        current_month = now.strftime("%B")
        current_year = now.strftime("%Y")
        
        # Load the current sheet
        try:
            sheet = await worksheet.worksheet(f"Payoutliste {current_month} {current_year}")
        except gspread.exceptions.WorksheetNotFound:
            return False, f"Die Payoutliste für {current_month} {current_year} existiert noch nicht"
        
        # Helper functions to parse member names
        def get_company(member: discord.Member):
            user_roles_ = member.roles
            user_role_ids_ = [role.id for role in user_roles_]
            company = None
                
            for user_role_id in user_role_ids_:
                if str(user_role_id) in spreadsheet_role_settings["company_role"]:
                    company = spreadsheet_role_settings["company_role"][str(user_role_id)]
                    break

            return company

        def full_parse(member):
            member_name = parse_display_name(member)
            member_name = member_name.split(" | ")[0]
            member_name = member_name.split(" I ")[0]
            member_name = member_name.replace("🏮 ", "")
            member_name = member_name.replace("🏮", "")
            member_name = member_name.replace(" ", "")
            return member_name
        
        def full_parse_specced(member):
            member_name = parse_display_name(member)
            member_name = member_name.split(" | ")[0]
            member_name = member_name.split(" I ")[0]
            member_name = member_name.replace("🏮 ", "")
            member_name = member_name.replace("🏮", "")
            return member_name
        
        # Get users with company roles
        guild = message.guild
        discord_memeber_list = [(full_parse(member), get_company(member)) for member in guild.members if get_company(member) is not None]
        discord_memeber_list_orig_names = [(full_parse_specced(member), get_company(member)) for member in guild.members if get_company(member) is not None]
        
        # Check if message is already processed
        data = await raidhelper_id_manager.load()
        mesage_ids = data.get("raidhelper_message_ids", [])
        
        if message.id in mesage_ids:
            return False, "Diese Raidhelper-Nachricht wurde bereits verarbeitet"
        
        # Extract data from the message
        raidhelper_usernames, dt = await extract_raidhelper_data(message)
        
        if not raidhelper_usernames or not dt:
            return False, "Keine gültigen Raidhelper-Daten in dieser Nachricht gefunden"
        
        # Process the data
        result = await process_raidhelper_for_payoutlist(sheet, discord_memeber_list, discord_memeber_list_orig_names, 
                                                       raidhelper_usernames, dt, event_type)
        
        if result:
            # Mark message as processed
            if "raidhelper_message_ids" not in data:
                data["raidhelper_message_ids"] = []
                
            data["raidhelper_message_ids"].append(message.id)
            await raidhelper_id_manager.save(data)
            
            return True, f"Raidhelper-Event vom {dt.strftime('%d.%m.%Y')} mit {len(raidhelper_usernames)} Teilnehmern erfolgreich verarbeitet"
        else:
            return False, "Fehler beim Verarbeiten der Raidhelper-Daten"
            
    except Exception as e:
        log.error(f"Error processing Raidhelper message: {str(e)}")
        log.error(traceback.format_exc())
        return False, f"Fehler: {str(e)}"

async def add_new_members_to_payoutlist(sheet, discord_member_list_orig_names):
    """
    Adds new members with company roles to the payoutlist
    """
    # Get current members in the payoutlist
    A_col = await sheet.get_values("F1:F300", major_dimension="COLUMNS")
    A_col = A_col[0]
    A_col = A_col[COLUMN_START_OFFSET:]
    
    # Check for new members that aren't in the sheet yet
    for member_data in discord_member_list_orig_names:
        discord_username, company = member_data
        
        # Skip members already in the sheet
        if discord_username in A_col:
            continue
            
        # Add new member to the sheet
        row = find_free_cell_in_column(A_col)
        
        name_update = []
        name_update.append({
            'range': f"{Column.NAME.value}{row}",
            'values': [[discord_username]]
        })
        await sheet.batch_update(name_update, value_input_option=gspread.utils.ValueInputOption.raw)
        
        # Set up the formulas and company
        await sheet.update_cell(row, 1, f'=(ZÄHLENWENNS($J$4:$AAA$4; "*Push*";$J$3:$AAA$3;"*Teilnehmer*";J{row}:AAA{row}; ">0") + ZÄHLENWENNS($J$4:$AAA$4; "*Push*";$J$3:$AAA$3;"*Teilnehmer*";J{row}:AAA{row}; "x"))/$A$5')
        await sheet.update_cell(row, 2, f'=(ZÄHLENWENNS($J$4:$AAA$4; "*Krieg*";$J$3:$AAA$3;"*Teilnehmer*";J{row}:AAA{row}; ">0") + ZÄHLENWENNS($J$4:$AAA$4; "*Krieg*";$J$3:$AAA$3;"*Teilnehmer*";J{row}:AAA{row}; "x"))/$B$5')
        await sheet.update_cell(row, 3, f'=(ZÄHLENWENNS($J$3:$AAA$3;"*Raidhelper*";J{row}:AAA{row}; ">0") + ZÄHLENWENNS($J$3:$AAA$3;"*Raidhelper*";J{row}:AAA{row}; "x"))/$C$5')
        await sheet.update_cell(row, 4, f'=(ZÄHLENWENNS($J$3:$AAA$3;"*VOD*";J{row}:AAA{row}; ">0") + ZÄHLENWENNS($J$3:$AAA$3;"*VOD*";J{row}:AAA{row}; "x"))/$D$5')
        await sheet.update_cell(row, 5, company)
        await sheet.update_cell(row, 7, f'=SUMME(I{row}:AAA{row})')
        await sheet.update_cell(row, 8, f'=G{row}*$H$3')
        
        log.info(f"Added new member to payoutlist: {discord_username}")

async def cleanup_payoutlist(sheet, discord_member_list_orig_names):
    """
    Removes members from the payoutlist that are no longer in the company/server
    """
    # Get the list of members currently in the payoutlist
    A_col = await sheet.get_values("F1:F300", major_dimension="COLUMNS")
    A_col = A_col[0]
    A_col = A_col[COLUMN_START_OFFSET:]
    
    # Get Discord members with company roles
    discord_members = [member[0] for member in discord_member_list_orig_names]
    
    # Find members to remove (in sheet but not in Discord with company role)
    batch_clear = []
    for i, member_name in enumerate(A_col):
        if member_name and member_name not in discord_members:
            row = i + 1 + COLUMN_START_OFFSET
            batch_clear.append(f"A{row}:AAA{row}")
            log.info(f"Removing member from payoutlist: {member_name}")
    
    # Clear rows for members to remove
    if batch_clear:
        await sheet.batch_clear(batch_clear)
        log.info(f"Cleared {len(batch_clear)} members from payoutlist")

async def update_payoutlist(bot: discord.Client, client: gspread_asyncio.AsyncioGspreadClientManager, parse_display_name: callable, spread_settings: jsonFileManager.JsonFileManager, gp_channel_manager: jsonFileManager.JsonFileManager):
    async with spreadsheet_payout_update:
        try:
            await _update_payoutlist(bot, client, parse_display_name, spread_settings, gp_channel_manager)
        except gspread.exceptions.APIError as e:
            channel = await gp_channel_manager.load()
            error_channel = bot.get_channel(channel.get("error_log_channel", 0))
            if error_channel:
                stacktrace = traceback.format_exc()
                
                # Speichere den Stacktrace in einem in-memory String-Stream
                file_obj = io.StringIO(stacktrace)
                
                # Erstelle ein discord.File-Objekt, das als "stacktrace.txt" gesendet wird
                discord_file = discord.File(fp=file_obj, filename="stacktrace.txt")
                
                # Sende die Nachricht mit der angehängten Datei
                await error_channel.send(content=f"# {__file__}\n```{json.dumps(e.response.json(), indent=4)}```", file=discord_file)

async def add_member_to_payoutlist(bot: discord.Client, client: gspread_asyncio.AsyncioGspreadClientManager, member: discord.Member, parse_display_name: callable, spread_settings: jsonFileManager.JsonFileManager):
    """
    Add a single member to the payoutlist when they get a company role
    """
    async with spreadsheet_payout_update:
        try:
            # Open the worksheet
            auth = await client.authorize()
            spreadsheet_role_settings = await spread_settings.load()
            
            # Check if member has a company role
            company = None
            for role in member.roles:
                if str(role.id) in spreadsheet_role_settings.get("company_role", {}):
                    company = spreadsheet_role_settings["company_role"][str(role.id)]
                    break
                    
            if not company:
                return  # Member doesn't have a company role
                
            worksheet = await auth.open(spreadsheet_role_settings["document_id"])
            
            # Get current month name and year
            now = datetime.datetime.now()
            current_month = now.strftime("%B")  # Full month name
            current_year = now.strftime("%Y")   # Year
            
            # Get the sheet
            try:
                sheet = await worksheet.worksheet(f"Payoutliste {current_month} {current_year}")
            except gspread.exceptions.WorksheetNotFound:
                # Sheet doesn't exist yet - will be created in the next update_payoutlist run
                return
                
            # Format username
            username = parse_display_name(member)
            username = username.split(" | ")[0]
            username = username.split(" I ")[0]
            username = username.replace("🏮 ", "")
            username = username.replace("🏮", "")
            
            # Check if already in the sheet
            A_col = await sheet.get_values("F1:F300", major_dimension="COLUMNS")
            A_col = A_col[0]
            A_col = A_col[COLUMN_START_OFFSET:]
            
            if username in A_col:
                # Already in sheet, update company if needed
                row = A_col.index(username) + 1 + COLUMN_START_OFFSET
                # Get the cell object first, then access its value
                cell = await sheet.cell(row, 5)
                current_company = cell.value
                
                if current_company != company:
                    await sheet.update_cell(row, 5, company)
                    log.info(f"Updated company for {username} to {company}")
            else:
                # Add to sheet
                row = find_free_cell_in_column(A_col)
                
                name_update = []
                name_update.append({
                    'range': f"{Column.NAME.value}{row}",
                    'values': [[username]]
                })
                await sheet.batch_update(name_update, value_input_option=gspread.utils.ValueInputOption.raw)
                
                # Set up the formulas and company
                await sheet.update_cell(row, 1, f'=(ZÄHLENWENNS($J$4:$AAA$4; "*Push*";$J$3:$AAA$3;"*Teilnehmer*";J{row}:AAA{row}; ">0") + ZÄHLENWENNS($J$4:$AAA$4; "*Push*";$J$3:$AAA$3;"*Teilnehmer*";J{row}:AAA{row}; "x"))/$A$5')
                await sheet.update_cell(row, 2, f'=(ZÄHLENWENNS($J$4:$AAA$4; "*Krieg*";$J$3:$AAA$3;"*Teilnehmer*";J{row}:AAA{row}; ">0") + ZÄHLENWENNS($J$4:$AAA$4; "*Krieg*";$J$3:$AAA$3;"*Teilnehmer*";J{row}:AAA{row}; "x"))/$B$5')
                await sheet.update_cell(row, 3, f'=(ZÄHLENWENNS($J$3:$AAA$3;"*Raidhelper*";J{row}:AAA{row}; ">0") + ZÄHLENWENNS($J$3:$AAA$3;"*Raidhelper*";J{row}:AAA{row}; "x"))/$C$5')
                await sheet.update_cell(row, 4, f'=(ZÄHLENWENNS($J$3:$AAA$3;"*VOD*";J{row}:AAA{row}; ">0") + ZÄHLENWENNS($J$3:$AAA$3;"*VOD*";J{row}:AAA{row}; "x"))/$D$5')
                await sheet.update_cell(row, 5, company)
                await sheet.update_cell(row, 7, f'=SUMME(I{row}:AAA{row})')
                await sheet.update_cell(row, 8, f'=G{row}*$H$3')
                
                log.info(f"Added member {username} to payoutlist with company {company}")
                
        except Exception as e:
            log.error(f"Error adding member to payoutlist: {str(e)}")
            log.error(traceback.format_exc())
