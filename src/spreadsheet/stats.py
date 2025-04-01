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
        member_name = member_name.replace(" ", "")
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

    # print(A_col)

    embed = discord.Embed(title=f"Stats", color=discord.Color.blurple())
    embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.avatar.url)

    async def get_quota(sheet_ref, month_label, year_label):
        # Return quota data (list of values) if member exists in the sheet, otherwise None.
        A_col = await sheet_ref.get_values(users, major_dimension="COLUMNS")
        A_col = A_col[0]
        if member_name in A_col:
            row = A_col.index(member_name) + 1 + COLUMN_START_OFFSET
            quota_range = f"{Column.QUOTA_RACES.value}{row}:{Column.QUOTA_VOD.value}{row}"
            quota_data = await sheet_ref.get_values(quota_range, major_dimension="ROWS")
            return quota_data[0] if quota_data else None
        return None

    # Define the users range once
    users = f"{Column.NAME.value}{COLUMN_START_OFFSET + 1}:{Column.NAME.value}200"

    # Get quotas for current and last month
    current_quota = await get_quota(sheet, current_month, current_year)
    last_month_quota = await get_quota(last_month_sheet, last_month_name, last_month_year)

    if current_quota is None:
        # Nutzer im aktuellen Monat nicht gefunden: Eventuell, weil der Bot noch Vorbereitungen trifft,
        # der Nutzer noch verifiziert wird oder es noch keinen neuen Raid-Helper in diesem Monat gab.
        if last_month_quota is not None:
            embed.add_field(
                name=f"{last_month_name} {last_month_year}",
                value=(
                    f"- **Races**: {last_month_quota[0]}\n"
                    f"- **Wars**: {last_month_quota[1]}\n"
                    f"- **Raidhelper**: {last_month_quota[2]}\n"
                    f"- **VOD**: {last_month_quota[3]}\n\n"
                ),
                inline=False
            )
            embed.add_field(
                name=f"{current_month} {current_year}",
                value=(
                    "Deine Daten für diesen Monat sind noch nicht verfügbar.\n"
                    "Möglicherweise befindet sich der Bot noch in den Vorbereitungen, du wirst gerade verifiziert oder der Raid-Helper wurde noch nicht aktualisiert."
                ),
                inline=False
            )
        else:
            embed.add_field(
                name="Teilnahme",
                value=(
                    "Deine Teilnahme wurde im aktuellen Monat nicht gefunden.\n"
                    "Dies kann daran liegen, dass der Bot noch Vorbereitungen trifft, du eventuell noch in der Verifizierung bist oder es in diesem Monat noch keinen neuen Raid-Helper gab."
                ),
                inline=False
            )
        await interaction.edit_original_response(embed=embed)
        return

    # Member found in current month: add current month quota data
    embed.add_field(
        name=f"{current_month} {current_year}",
        value=(
            "*Hinweis: Dies ist der aktuelle Monat und möglicherweise sind noch nicht alle Daten eingepflegt.*\n"
            f"- **Races**: {current_quota[0]}\n"
            f"- **Wars**: {current_quota[1]}\n"
            f"- **Raidhelper**: {current_quota[2]}\n"
            f"- **VOD**: {current_quota[3]}\n\n"
        ),
        inline=False
    )

    # Add last month quota data or note its absence
    if last_month_quota is not None:
        embed.add_field(
            name=f"{last_month_name} {last_month_year}",
            value=(
                f"- **Races**: {last_month_quota[0]}\n"
                f"- **Wars**: {last_month_quota[1]}\n"
                f"- **Raidhelper**: {last_month_quota[2]}\n"
                f"- **VOD**: {last_month_quota[3]}"
            ),
            inline=False
        )
    else:
        embed.add_field(
            name=f"{last_month_name} {last_month_year}",
            value=(
                "Du wurdest nicht in der Payoutliste gefunden.\n"
                "Aufgrund von Änderungen (Breaking Changes) in der Payoutliste können der Februar sowie "
                "alle älteren Monate momentan nicht angezeigt werden.\nWir bitten um dein Verständnis."
            ),
            inline=False
        )

    await interaction.edit_original_response(embed=embed)