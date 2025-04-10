import gspread.utils
import gspread_asyncio
import gspread
import discord
from enum import Enum
import jsonFileManager
import asyncio
import datetime
import locale


# Setze die Locale auf Deutsch
locale.setlocale(locale.LC_TIME, "de_DE.UTF-8")

spreadsheet_payout_update = asyncio.Lock()

class Column(Enum):
    URLAUB = 'K'

OFFSET = 9

async def _abwesenheit(client: gspread_asyncio.AsyncioGspreadClientManager, interaction: discord.Interaction, parse_display_name: callable, spread_settings: jsonFileManager.JsonFileManager, date: str):

    member = interaction.user

    # check if member has a company role
    spreadsheet_role_settings = await spread_settings.load()
    if "document_id" not in spreadsheet_role_settings:
        return

    user_roles = member.roles
    user_role_ids = [role.id for role in user_roles]

    def get_company(member: discord.Member):
        user_roles_ = member.roles
        user_role_ids_ = [role.id for role in user_roles_]
        company = None
            
        for user_role_id in user_role_ids_:
            if str(user_role_id) in spreadsheet_role_settings["company_role"]:
                company = spreadsheet_role_settings["company_role"][str(user_role_id)]
                break

        return company
    
    company = get_company(member)
    if company is None:
        return

    def full_parse(member):
        # Parse the display name
        member_name = parse_display_name(member)

        # Mach aus namen wie "Dirty Torty | Jan" -> "Dirty Tory"
        member_name = member_name.split(" | ")[0]
        member_name = member_name.split(" I ")[0]
        member_name = member_name.replace("🏮 ", "")
        member_name = member_name.replace("🏮", "")
        return member_name
    
    member_name = full_parse(member)
    
    # Open the worksheet
    auth = await client.authorize()
    worksheet = await auth.open(spreadsheet_role_settings["document_id"])
    sheet = await worksheet.worksheet("Memberliste")

    # Get the column A
    A_col = await sheet.get_values("A1:A114", major_dimension="COLUMNS")
    A_col = A_col[0]

    # Delete first 9 entries
    A_col = A_col[OFFSET:]

    row_number = None
    # Check if member is already in the list
    if member_name in A_col:
        row_number = A_col.index(member_name) + 1 + OFFSET

    if row_number is None:
        return
    
    await sheet.update_acell(f"{Column.URLAUB.value}{row_number}", date)


class UrlaubsModal(discord.ui.Modal, title="Abwesenheit eintragen"):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Heutiges Datum als String im Format YYYY-MM-DD
        today = datetime.date.today().strftime("%Y-%m-%d")
        # Erstelle die TextInput-Felder dynamisch mit dem heutigen Datum als Default
        self.start = discord.ui.TextInput(
            label="Startdatum (YYYY-MM-DD)",
            placeholder=today,
            default=today
        )
        self.end = discord.ui.TextInput(
            label="Enddatum (YYYY-MM-DD)",
            placeholder=today,
            default=today
        )

        self.grund = discord.ui.TextInput(
            label="Grund (Optional)",
            placeholder="(Optional)",
            required=False
        )
        # Füge die TextInputs dem Modal hinzu
        self.add_item(self.start)
        self.add_item(self.end)
        self.add_item(self.grund)

    def fake_init(self, client: gspread_asyncio.AsyncioGspreadClientManager, parse_display_name: callable, spread_settings: jsonFileManager.JsonFileManager):
        self.P_client = client
        self.P_parse_display_name = parse_display_name
        self.P_spread_settings = spread_settings

    async def on_submit(self, interaction: discord.Interaction):
        try:
            start_date = datetime.datetime.strptime(self.start.value, "%Y-%m-%d").date()
            end_date = datetime.datetime.strptime(self.end.value, "%Y-%m-%d").date()
            if start_date > end_date:
                await interaction.response.send_message("Das Startdatum darf nicht nach dem Enddatum liegen!", ephemeral=True)
                return
            await interaction.response.send_message(f"Abwesenehit eingetragen von {start_date} bis {end_date}!")
            await _abwesenheit(self.P_client, interaction, self.P_parse_display_name, self.P_spread_settings, f"{start_date} - {end_date} {self.grund.value}")
        except ValueError:
            await interaction.response.send_message("Bitte gib die Daten im Format YYYY-MM-DD ein.", ephemeral=True)