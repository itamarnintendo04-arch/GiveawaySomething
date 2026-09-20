import discord
from discord.ext import commands
from discord import app_commands
from aiohttp import web
import asyncio
import random
import datetime
import os
import re

# --- FAKE LANDING PAGE WEB SERVER ---
HTML_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GiveawaySomething Bot</title>
    <style>
        body {
            background-color: #0f172a;
            color: #ffffff;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            height: 100vh;
            margin: 0;
            text-align: center;
        }
        .container {
            background: #1e293b;
            padding: 40px;
            border-radius: 16px;
            box-shadow: 0 10px 25px rgba(0,0,0,0.5);
            max-width: 400px;
            width: 90%;
            border: 1px solid #334155;
        }
        h1 { color: #38bdf8; margin-bottom: 10px; }
        p { color: #94a3b8; font-size: 1.1em; margin-bottom: 25px; }
        .status {
            display: inline-block;
            padding: 8px 16px;
            background-color: #22c55e;
            color: #ffffff;
            border-radius: 20px;
            font-weight: bold;
            font-size: 0.9em;
            margin-bottom: 20px;
        }
        .btn {
            display: inline-block;
            background-color: #5865F2;
            color: white;
            padding: 12px 24px;
            text-decoration: none;
            border-radius: 8px;
            font-weight: bold;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🎉 GiveawaySomething</h1>
        <div class="status">● Bot Status: ONLINE</div>
        <p>The ultimate giveaway & quick-drop bot for your Discord server.</p>
        <a href="#" class="btn">Add to Discord</a>
    </div>
</body>
</html>
"""

async def handle_index(request):
    return web.Response(text=HTML_PAGE, content_type='text/html')

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_index)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"Web server started on port {port}")


# --- DISCORD BOT SETUP ---
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Active giveaways store: message_id -> set of user_ids
active_giveaways = {}

# Time parser helper function
def parse_duration(time_str: str) -> int:
    match = re.match(r"^(\d+)([smhd])?$", time_str.lower().strip())
    if not match:
        return -1
    
    amount = int(match.group(1))
    unit = match.group(2)
    
    if unit == 'm':
        return amount * 60
    elif unit == 'h':
        return amount * 3600
    elif unit == 'd':
        return amount * 86400
    else:
        return amount # Default to seconds


class GiveawayView(discord.ui.View):
    def __init__(self, message_id: int):
        super().__init__(timeout=None)
        self.message_id = message_id

    @discord.ui.button(label="Join 🎉 (0)", style=discord.ButtonStyle.success, custom_id="join_giveaway_btn")
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
        
        button.label = f"Join 🎉 ({len(participants)})"
        
        embed = interaction.message.embeds[0]
        embed.set_footer(
            text=f"GiveawaySomething • {len(participants)} Participants • Hosted by ItamaRos",
            icon_url="https://cdn-icons-png.flaticon.com/512/3135/3135715.png"
        )
        await interaction.message.edit(embed=embed, view=self)
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

        self.children[0].label = f"Join 🎉 ({len(participants)})"

        embed = interaction.message.embeds[0]
        embed.set_footer(
            text=f"GiveawaySomething • {len(participants)} Participants • Hosted by ItamaRos",
            icon_url="https://cdn-icons-png.flaticon.com/512/3135/3135715.png"
        )
        await interaction.message.edit(embed=embed, view=self)
        await interaction.response.send_message("You have left the giveaway.", ephemeral=True)


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

        # --- DM THE WINNER ---
        try:
            await winner.send(f"⚡ Congratulations! You claimed the drop for **{self.prize}** in **{interaction.guild.name}**!")
        except discord.Forbidden:
            pass # Ignoring if user disabled DMs

        # --- DM THE HOST ---
        if self.host.id != winner.id:
            try:
                await self.host.send(f"⚡ Your drop for **{self.prize}** in **{interaction.guild.name}** was successfully claimed by {winner.mention}!")
            except discord.Forbidden:
                pass


@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user.name} (GiveawaySomething) - Slash commands synced!")


@bot.tree.command(name="giveaway", description="Start a timed giveaway! (Admin Only)")
@app_commands.describe(
    duration="Time format: 30s, 10m, 2h, 1d",
    prize="What are you giving away?",
    winners="Number of winners (default: 1)"
)
async def start_giveaway(interaction: discord.Interaction, duration: str, prize: str, winners: int = 1):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You need Administrator permissions to start a giveaway!", ephemeral=True)
        return

    seconds = parse_duration(duration)
    if seconds <= 0:
        await interaction.response.send_message("❌ Invalid duration format! Use formats like `30s`, `10m`, `2h`, or `1d`.", ephemeral=True)
        return

    end_time = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)
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

    # Wait for the giveaway to finish
    await asyncio.sleep(seconds)

    participants_list = list(active_giveaways.get(msg.id, set()))
    if msg.id in active_giveaways:
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
        
        # DM Host that nobody joined
        try:
            await interaction.user.send(f"📢 Your giveaway for **{prize}** in **{interaction.guild.name}** ended with no participants.")
        except discord.Forbidden:
            pass

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

        # --- DM THE WINNERS ---
        for w_id in winner_ids:
            member = interaction.guild.get_member(w_id)
            if member:
                try:
                    await member.send(f"🎉 Congratulations! You won the giveaway for **{prize}** in **{interaction.guild.name}**!")
                except discord.Forbidden:
                    pass

        # --- DM THE HOST ---
        try:
            await interaction.user.send(f"📢 Your giveaway for **{prize}** in **{interaction.guild.name}** has ended!\n🏆 Winner(s): {winner_mentions}")
        except discord.Forbidden:
            pass


@bot.tree.command(name="participants", description="See all participants of an active giveaway (Admin Only)")
@app_commands.describe(message_id="The ID of the giveaway message")
async def list_participants(interaction: discord.Interaction, message_id: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You need Administrator permissions to use this command!", ephemeral=True)
        return

    try:
        msg_id = int(message_id)
    except ValueError:
        await interaction.response.send_message("❌ Please enter a valid message ID!", ephemeral=True)
        return

    if msg_id not in active_giveaways:
        await interaction.response.send_message("❌ Giveaway not found or already ended!", ephemeral=True)
        return

    participants = active_giveaways[msg_id]
    if not participants:
        await interaction.response.send_message("There are currently no participants in this giveaway.", ephemeral=True)
        return

    user_mentions = "\n".join([f"• <@{u_id}>" for u_id in participants])
    embed = discord.Embed(
        title="📋 Giveaway Participants",
        description=f"**Total:** {len(participants)}\n\n{user_mentions}",
        color=discord.Color.blue()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="remove_participant", description="Remove a user from an active giveaway (Admin Only)")
@app_commands.describe(message_id="The ID of the giveaway message", user="The user to remove")
async def remove_participant(interaction: discord.Interaction, message_id: str, user: discord.Member):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You need Administrator permissions to use this command!", ephemeral=True)
        return

    try:
        msg_id = int(message_id)
    except ValueError:
        await interaction.response.send_message("❌ Please enter a valid message ID!", ephemeral=True)
        return

    if msg_id not in active_giveaways:
        await interaction.response.send_message("❌ Giveaway not found or already ended!", ephemeral=True)
        return

    participants = active_giveaways[msg_id]
    if user.id not in participants:
        await interaction.response.send_message(f"❌ {user.mention} is not in this giveaway!", ephemeral=True)
        return

    participants.remove(user.id)

    try:
        msg = await interaction.channel.fetch_message(msg_id)
        embed = msg.embeds[0]
        embed.set_footer(
            text=f"GiveawaySomething • {len(participants)} Participants • Hosted by ItamaRos",
            icon_url="https://cdn-icons-png.flaticon.com/512/3135/3135715.png"
        )
        
        view = GiveawayView(msg_id)
        view.children[0].label = f"Join 🎉 ({len(participants)})"
        await msg.edit(embed=embed, view=view)
    except Exception:
        pass

    await interaction.response.send_message(f"✅ Successfully removed {user.mention} from the giveaway!", ephemeral=True)


@bot.tree.command(name="drop", description="Start a drop giveaway! (Admin Only)")
@app_commands.describe(prize="What is the drop prize?")
async def start_drop(interaction: discord.Interaction, prize: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You need Administrator permissions to start a drop!", ephemeral=True)
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


async def main():
    TOKEN = os.getenv("DISCORD_TOKEN")
    if not TOKEN:
        print("Error: DISCORD_TOKEN environment variable is missing!")
        return

    await start_web_server()
    await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
