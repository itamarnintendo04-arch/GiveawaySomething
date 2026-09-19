import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import random
import datetime
import os

# Set up bot intents
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Active giveaways store: message_id -> set of user_ids
active_giveaways = {}

class GiveawayView(discord.ui.View):
    def __init__(self, message_id: int):
        super().__init__(timeout=None)
        self.message_id = message_id

    @discord.ui.button(label="Join 🎉", style=discord.ButtonStyle.success, custom_id="join_giveaway_btn")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        msg_id = interaction.message.id
        user_id = interaction.user.id

        if msg_id not in active_giveaways:
            await interaction.response.send_message("This giveaway has ended!", ephemeral=True)
            return

        participants = active_giveaways[msg_id]
        if user_id in participants:
            await interaction.response.send_message("You are already in this giveaway!", ephemeral=True)
            return

        participants.add(user_id)
        
        # Update embed footer with participant count
        embed = interaction.message.embeds[0]
        embed.set_footer(
            text=f"GiveawaySomething • {len(participants)} Participants • Hosted by ItamaRos",
            icon_url="https://cdn-icons-png.flaticon.com/512/3135/3135715.png"
        )
        await interaction.message.edit(embed=embed)
        await interaction.response.send_message("🎉 You have successfully joined the giveaway!", ephemeral=True)

    @discord.ui.button(label="Leave ✖️", style=discord.ButtonStyle.danger, custom_id="leave_giveaway_btn")
    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        msg_id = interaction.message.id
        user_id = interaction.user.id

        if msg_id not in active_giveaways:
            await interaction.response.send_message("This giveaway has ended!", ephemeral=True)
            return

        participants = active_giveaways[msg_id]
        if user_id not in participants:
            await interaction.response.send_message("You haven't joined this giveaway yet!", ephemeral=True)
            return

        participants.remove(user_id)

        # Update embed footer with participant count
        embed = interaction.message.embeds[0]
        embed.set_footer(
            text=f"GiveawaySomething • {len(participants)} Participants • Hosted by ItamaRos",
            icon_url="https://cdn-icons-png.flaticon.com/512/3135/3135715.png"
        )
        await interaction.message.edit(embed=embed)
        await interaction.response.send_message(" You have left the giveaway.", ephemeral=True)


class DropView(discord.ui.View):
    def __init__(self, prize: str, host: discord.User):
        super().__init__(timeout=None)
        self.prize = prize
        self.host = host
        self.claimed = False

    @discord.ui.button(label="CLAIM DROP! ⚡", style=discord.ButtonStyle.success, custom_id="claim_drop_btn")
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.claimed:
            await interaction.response.send_message("This drop was already claimed!", ephemeral=True)
            return

        self.claimed = True
        winner = interaction.user

        # Disable button after claim
        button.disabled = True
        button.label = "CLAIMED! 🎁"
        button.style = discord.ButtonStyle.secondary

        embed = discord.Embed(
            title="⚡ DROP CLAIMED! ⚡",
            description=f"**Prize:** {self.prize}\n**Winner:** {winner.mention}\n**Hosted by:** {self.host.mention}",
            color=discord.Color.gold(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.set_footer(text="GiveawaySomething • Drop Ended")

        await interaction.message.edit(embed=embed, view=self)
        await interaction.response.send_message(f"🎉 Congratulations {winner.mention}! You claimed the drop for **{self.prize}**!")


@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user.name} (GiveawaySomething) - Slash commands synced!")


@bot.tree.command(name="giveaway", description="Start a timed giveaway!")
@app_commands.describe(
    duration="Duration in seconds",
    prize="What are you giving away?",
    winners="Number of winners (default: 1)"
)
async def start_giveaway(interaction: discord.Interaction, duration: int, prize: str, winners: int = 1):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need Administrator permissions to start a giveaway!", ephemeral=True)
        return

    end_time = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=duration)
    timestamp_format = f"<t:{int(end_time.timestamp())}:R>"

    embed = discord.Embed(
        title=f"🎉 {prize} 🎉",
        description=(
            f"Click the **Join 🎉** button below to enter!\n\n"
            f"⏱️ **Ends:** {timestamp_format}\n"
            f"👑 **Hosted by:** {interaction.user.mention}\n"
            f"🏆 **Winners:** {winners}"
        ),
        color=discord.Color.green(),
        timestamp=end_time
    )
    embed.set_thumbnail(url="https://cdn-icons-png.flaticon.com/512/3135/3135715.png")
    embed.set_footer(
        text="GiveawaySomething • 0 Participants • Hosted by ItamaRos",
        icon_url="https://cdn-icons-png.flaticon.com/512/3135/3135715.png"
    )

    await interaction.response.send_message("Giveaway created!", ephemeral=True)
    msg = await interaction.channel.send(embed=embed)
    
    view = GiveawayView(msg.id)
    await msg.edit(view=view)

    active_giveaways[msg.id] = set()

    # Wait for duration
    await asyncio.sleep(duration)

    # Giveaway ended
    participants_list = list(active_giveaways.get(msg.id, set()))
    del active_giveaways[msg.id]

    if not participants_list:
        ended_embed = discord.Embed(
            title=f"🎉 {prize} (ENDED) 🎉",
            description=f"**Winner:** No participants registered.\n**Hosted by:** {interaction.user.mention}",
            color=discord.Color.red()
        )
        ended_embed.set_footer(text="GiveawaySomething • Ended")
        await msg.edit(embed=ended_embed, view=None)
        await interaction.channel.send(f"The giveaway for **{prize}** ended, but nobody joined! 😢")
    else:
        num_winners = min(winners, len(participants_list))
        winner_ids = random.sample(participants_list, num_winners)
        winner_mentions = ", ".join([f"<@{w_id}>" for w_id in winner_ids])

        ended_embed = discord.Embed(
            title=f"🎉 {prize} (ENDED) 🎉",
            description=f"🏆 **Winner(s):** {winner_mentions}\n👑 **Hosted by:** {interaction.user.mention}",
            color=discord.Color.gold()
        )
        ended_embed.set_footer(text="GiveawaySomething • Ended")
        await msg.edit(embed=ended_embed, view=None)
        await interaction.channel.send(f"🎉 Congratulations {winner_mentions}! You won **{prize}**! 🎁")


@bot.tree.command(name="drop", description="Start a drop giveaway! First person to click wins!")
@app_commands.describe(prize="What is the drop prize?")
async def start_drop(interaction: discord.Interaction, prize: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need Administrator permissions to start a drop!", ephemeral=True)
        return

    embed = discord.Embed(
        title="⚡ QUICK DROP! ⚡",
        description=f"**Prize:** {prize}\n**Hosted by:** {interaction.user.mention}\n\nFirst person to click **CLAIM DROP!** wins!",
        color=discord.Color.blue()
    )
    embed.set_footer(text="GiveawaySomething • Fast Drop")

    view = DropView(prize, interaction.user)
    await interaction.response.send_message("Drop created!", ephemeral=True)
    await interaction.channel.send(embed=embed, view=view)


# שליפת הטוקן מתוך משתני הסביבה (Environment Variables) ב-Render
TOKEN = os.getenv("DISCORD_TOKEN")

if TOKEN:
    bot.run(TOKEN)
else:
    print("Error: DISCORD_TOKEN environment variable is not set!")
