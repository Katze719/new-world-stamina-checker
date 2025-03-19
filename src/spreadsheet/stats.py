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
    QUOTA_RACES = 'A'
    QUOTA_WAR = 'B'
    QUOTA_RAIDHELPER = 'C'
    QUOTA_VOD = 'D'
    COMPANY = 'E'
    NAME = 'F'
    START = 'L'

# Offset for the first 6 entries
COLUMN_START_OFFSET = 6
ROW_START_OFFSET = 4

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


async def stats(client: gspread_asyncio.AsyncioGspreadClientManager, interaction: discord.Interaction, parse_display_name: callable, spread_settings: jsonFileManager.JsonFileManager):
    """
    Fetch the percentages from war participation
    """
    # check if member has a company role
    spreadsheet_role_settings = await spread_settings.load()
    if "document_id" not in spreadsheet_role_settings:
        return

    user_roles = interaction.user.roles
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
    
    company = get_company(interaction.user)
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
    
    member_name = full_parse(interaction.user)
    
    now = datetime.datetime.now()

    # Formatieren: Monat und Jahr

    # Open the worksheet
    auth = await client.authorize()
    worksheet = await auth.open(spreadsheet_role_settings["document_id"])

    current_month = now.strftime("%B")  # Vollständiger Monatsname
    current_year = now.strftime("%Y")   # Jahr
    sheet = await worksheet.worksheet(f"Payoutliste {current_month} {current_year}")

    last_month = now - datetime.timedelta(days=now.day)
    last_month_name = last_month.strftime("%B")
    last_month_year = last_month.strftime("%Y")
    last_month_sheet = await worksheet.worksheet(f"Payoutliste {last_month_name} {last_month_year}")

    # Get the column A
    users = f"{Column.NAME.value}{COLUMN_START_OFFSET + 1}:{Column.NAME.value}200"
    A_col = await sheet.get_values(users, major_dimension="COLUMNS")
    A_col = A_col[0]

    print(A_col)

    embed = discord.Embed(title=f"Stats", color=discord.Color.blurple())
    embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.avatar.url)

    if member_name not in A_col:
        embed.add_field(name="Teilnahme", value="Du hast diesen Monat noch nicht an einem War teilgenommen.", inline=False)
        await interaction.edit_original_response(embed=embed)
        return
    else:
        row_number = A_col.index(member_name) + 1 + COLUMN_START_OFFSET
        
        # send quota info to user
        quota = await sheet.get_values(f"{Column.QUOTA_RACES.value}{row_number}:{Column.QUOTA_VOD.value}{row_number}", major_dimension="ROWS")
        quota = quota[0]
        embed.add_field(name=f"{current_month} {current_year}", value=f"- **Races**: {quota[0]}\n- **Wars**: {quota[1]}\n- **Raidhelper**: {quota[2]}\n- **VOD**: {quota[3]}", inline=False)

        # add last month sheet quota
        A_col_last_month = await last_month_sheet.get_values(users, major_dimension="COLUMNS")
        A_col_last_month = A_col_last_month[0]
        if member_name in A_col_last_month:
            last_month_row = A_col_last_month.index(member_name) + 1 + COLUMN_START_OFFSET

            last_month_quota = await last_month_sheet.get_values(f"{Column.QUOTA_RACES.value}{last_month_row}:{Column.QUOTA_VOD.value}{last_month_row}", major_dimension="ROWS")
            last_month_quota = last_month_quota[0]
            embed.add_field(name=f"{last_month_name} {last_month_year}", value=f"- **Races**: {last_month_quota[0]}\n- **War**: {last_month_quota[1]}\n- **Raidhelper**: {last_month_quota[2]}\n- **VOD**: {last_month_quota[3]}", inline=False)
        else:
            embed.add_field(
                name=f"{last_month_name} {last_month_year}",
                value="Im vergangenen Monat hast du leider nicht an einem War teilgenommen.\nAufgrund von Änderungen (Breaking Changes) in der Payoutliste können der Februar sowie alle älteren Monate momentan nicht angezeigt werden.\nWir bitten um dein Verständnis.",
                inline=False
            )


        await interaction.edit_original_response(embed=embed)