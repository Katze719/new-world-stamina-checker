import discord
import datetime

# ---------- Hilfs-Utilities ---------- #
def norm(val: str) -> str:
    val = val.lower().strip()
    # Handle numeric values
    if val in {"1", "eins", "verbesserungswürdig"}:         return "1"
    if val in {"2", "zwei", "ausbaufähig"}:              return "2"
    if val in {"3", "drei", "mittel"}:                return "3"
    if val in {"4", "vier", "gut"}:                   return "4"
    if val in {"5", "fünf", "fuenf", "sehr gut"}:     return "5"
    
    # Backward compatibility
    if val in {"schlecht", "rot", "r", "s", "sehr schlecht"}:          return "2"
    if val in {"mittel", "gelb", "m", "y"}:           return "3"
    if val in {"gut", "grün", "g"}:                   return "4"
    
    return "nicht bewertet"

EMOJI = {
    "1": "🔴",         # Rot (sehr schlecht)
    "2": "🟠",         # Orange (schlecht)
    "3": "🟡",         # Gelb (mittel)
    "4": "🟢",         # Grün (gut)
    "5": "💯",         # Blau (sehr gut)
    "nicht bewertet": "⚪"
}

# Beschreibungen für die Bewertungen
RATING_DESCRIPTIONS = {
    "1": "Verbesserungswürdig",
    "2": "Ausbaufähig",
    "3": "Mittel",
    "4": "Gut",
    "5": "Sehr gut"
}

# ---------- Haupt-View ---------- #
class VodReviewMainView(discord.ui.View):
    def __init__(self, target: discord.Member):
        super().__init__(timeout=None)  # Set timeout to None for permanent views
        self.target = target
        self.ratings = {
            "positioning":        "",
            "pot_management":     "",
            "calling":            "",
            "group_play":         "",
            "stamina_management": "",
            "mechanics":          "",
        }
        self.notes = ""
        self.date = ""  # Added date field
        
        # Store channel and message IDs instead of message object
        self.channel_id = None
        self.message_id = None
        
        # For backward compatibility, we'll keep this but won't use it directly
        self.message = None
        
        # Bot reference will be set after initialization
        self.bot = None

    # Method to set message reference from interaction response
    def set_message(self, message):
        self.message = message
        self.channel_id = message.channel.id
        self.message_id = message.id

    # -------- Embed bauen & Nachricht aktualisieren -------- #
    async def refresh_message(self, view: discord.ui.View | None = None, bot = None):
        if not self.channel_id or not self.message_id:
            return
            
        embed = discord.Embed(
            title=f"VOD Review für {self.target.display_name}",
            color=discord.Color.blurple()
        )

        # Add date if available
        if self.date:
            embed.description = f"📅 Datum: {self.date}"

        nice = {
            "positioning":        "Positioning (P)",
            "pot_management":     "Pot Management (PM)",
            "calling":            "Calling (CL)",
            "group_play":         "Group Play (OP)",
            "stamina_management": "Stamina Management (SM)",
            "mechanics":          "Mechanics (MC)",
        }
        for key, label in nice.items():
            value = self.ratings[key]
            rating = norm(value) if value else "nicht bewertet"
            
            # Get appropriate description
            description = RATING_DESCRIPTIONS.get(rating, "–") if value else "–"
            
            embed.add_field(
                name=label,
                value=f"{EMOJI[rating]} {description}",
                inline=True
            )

        if self.notes:
            embed.add_field(name="Notizen", value=self.notes, inline=False)

        try:
            # Get channel by ID and then get message by ID
            if bot:
                channel = bot.get_channel(self.channel_id)
                if channel:
                    message = await channel.fetch_message(self.message_id)
                    if message:
                        await message.edit(embed=embed, view=view or self)
        except Exception as e:
            print(f"Error refreshing message: {e}")

    # ---------------- Buttons ---------------- #
    @discord.ui.button(label="Ratings 1 ▼ (P, PM, CL)", style=discord.ButtonStyle.secondary, row=0)
    async def ratings1(self, interaction: discord.Interaction, _):
        await interaction.response.edit_message(
            view=RatingsView(self, part=1)
        )

    @discord.ui.button(label="Ratings 2 ▼ (OP, SM, MC)", style=discord.ButtonStyle.secondary, row=0)
    async def ratings2(self, interaction: discord.Interaction, _):
        await interaction.response.edit_message(
            view=RatingsView(self, part=2)
        )

    @discord.ui.button(label="Datum 📅", style=discord.ButtonStyle.secondary, row=0)
    async def date_button(self, interaction: discord.Interaction, _):
        class DateModal(discord.ui.Modal, title="VOD Datum"):
            date_input = discord.ui.TextInput(
                label="Datum des VODs",
                placeholder="z.B. 2023-06-15 oder 15.06.2023",
                required=False,
                default=self.date or datetime.datetime.now().strftime("%d.%m.%Y")
            )

            async def on_submit(modal_self, inter: discord.Interaction):
                self.date = modal_self.date_input.value
                await inter.response.defer(ephemeral=True)
                await self.refresh_message(bot=inter.client)

        await interaction.response.send_modal(DateModal())

    @discord.ui.button(label="Notizen 📝", style=discord.ButtonStyle.secondary, row=0)
    async def notes_button(self, interaction: discord.Interaction, _):
        class NotesModal(discord.ui.Modal, title="Notizen"):
            notes = discord.ui.TextInput(
                label="Zusätzliche Notizen",
                style=discord.TextStyle.paragraph,
                required=False,
                default=self.notes or None
            )
            async def on_submit(modal_self, inter: discord.Interaction):
                self.notes = modal_self.notes.value
                await inter.response.defer(ephemeral=True)
                await self.refresh_message(bot=inter.client)

        await interaction.response.send_modal(NotesModal())

    @discord.ui.button(label="Absenden ✅", style=discord.ButtonStyle.success, row=0)
    async def submit(self, interaction: discord.Interaction, _):
        if "" in self.ratings.values():
            await interaction.response.send_message(
                "Bitte alle sechs Ratings ausfüllen, bevor du absendest.",
                ephemeral=True)
            return

        final = discord.Embed(
            title=f"VOD Review für {self.target.display_name}",
            description=f"Review erstellt von {interaction.user.display_name}",
            color=discord.Color.green()
        )

        # Add date to final review if available
        if self.date:
            final.add_field(name="Datum", value=f"📅 {self.date}", inline=False)
            
        names = {
            "positioning":        "Positioning (P)",
            "pot_management":     "Pot Management (PM)",
            "calling":            "Calling (CL)",
            "group_play":         "Group Play (OP)",
            "stamina_management": "Stamina Management (SM)",
            "mechanics":          "Mechanics (MC)",
        }
        for k, v in self.ratings.items():
            rating = norm(v)
            description = RATING_DESCRIPTIONS.get(rating, "–")
            final.add_field(name=names[k],
                            value=f"{EMOJI[rating]} {description}",
                            inline=True)
        if self.notes:
            final.add_field(name="Notizen", value=self.notes, inline=False)

        # Send final review as a new message
        await interaction.response.send_message(embed=final)
        
        # Delete the original message with the review UI
        try:
            # Try to delete using direct channel access
            if self.channel_id and self.message_id:
                channel = self.bot.get_channel(self.channel_id)
                if channel:
                    message = await channel.fetch_message(self.message_id)
                    if message:
                        await message.delete()
        except Exception as e:
            print(f"Error deleting message: {e}")

# ---------- View mit Selects (Teil 1 oder 2) ---------- #
class RatingsView(discord.ui.View):
    def __init__(self, main: VodReviewMainView, part: int):
        super().__init__(timeout=None)  # Set timeout to None here too
        self.main_view = main
        self.part = part

        if part == 1:
            cfg = [
                ("positioning",       "Positioning (P)"),
                ("pot_management",    "Pot Management (PM)"),
                ("calling",           "Calling (CL)"),
            ]
        else:
            cfg = [
                ("group_play",        "Group Play (OP)"),
                ("stamina_management","Stamina Management (SM)"),
                ("mechanics",         "Mechanics (MC)"),
            ]

        # Für jede Kategorie ein eigener Select
        for key, label in cfg:
            self.add_item(self.RatingSelect(key, label, main_view=main))

        # Zurück-Button
        self.add_item(self.BackButton(main))

    # ---------- Select-Komponente ---------- #
    class RatingSelect(discord.ui.Select):
        def __init__(self, key: str, label: str, main_view: VodReviewMainView):
            # Current rating value
            current_rating = norm(main_view.ratings.get(key, ""))
            
            # Create 5 select options with appropriate colors
            opts = [
                discord.SelectOption(
                    label="1 - Verbesserungswürdig", 
                    value="1",
                    emoji="🔴", 
                    default=current_rating=="1"
                ),
                discord.SelectOption(
                    label="2 - Ausbaufähig", 
                    value="2",
                    emoji="🟠", 
                    default=current_rating=="2"
                ),
                discord.SelectOption(
                    label="3 - Mittel", 
                    value="3",
                    emoji="🟡", 
                    default=current_rating=="3"
                ),
                discord.SelectOption(
                    label="4 - Gut", 
                    value="4",
                    emoji="🟢", 
                    default=current_rating=="4"
                ),
                discord.SelectOption(
                    label="5 - Sehr gut", 
                    value="5",
                    emoji="💯", 
                    default=current_rating=="5"
                )
            ]
            
            super().__init__(
                placeholder=label,
                options=opts,
                min_values=1, 
                max_values=1
            )
            self.key = key
            self.main_view = main_view

        async def callback(self, interaction: discord.Interaction):
            self.main_view.ratings[self.key] = self.values[0]
            # Embed updaten, aber in derselben View bleiben
            await self.main_view.refresh_message(view=self.view, bot=interaction.client)
            await interaction.response.defer()

    # ---------- Zurück-Button ---------- #
    class BackButton(discord.ui.Button):
        def __init__(self, main_view: VodReviewMainView):
            super().__init__(label="Zurück", style=discord.ButtonStyle.primary, row=4)
            self.main_view = main_view
        async def callback(self, interaction: discord.Interaction):
            await self.main_view.refresh_message()
            await interaction.response.edit_message(view=self.main_view)