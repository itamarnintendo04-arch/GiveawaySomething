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
        body { background-color: #0f172a; color: #ffffff; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; margin: 0; text-align: center; }
        .container { background: #1e293b; padding: 40px; border-radius: 16px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); max-width: 400px; width: 90%; border: 1px solid #334155; }
        h1 { color: #38bdf8; margin-bottom: 10px; }
        p { color: #94a3b8; font-size: 1.1em; margin-bottom: 25px; }
        .status { display: inline-block; padding: 8px 16px; background-color: #22c55e; color: #ffffff; border-radius: 20px; font-weight: bold; font-size: 0.9em; margin-bottom: 20px; }
        .btn { display: inline-block; background-color: #5865F2; color: white; padding: 12px 24px; text-decoration: none; border-radius: 8px; font-weight: bold; }
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


# --- DISCORD BOT CLASS WITH GRACEFUL SHUTDOWN ---
class GiveawayBot(commands.Bot):
    async def close(self):
        print("Render is updating/restarting! Updating bot status before shutdown...")
        try:
            # Update status before Render shuts down the server
            await self.change_presence(
                status=discord.Status.dnd,
                activity=discord.CustomActivity(name="⏳ Server update in progress, please wait...")
            )
            await asyncio.sleep(2)  # Give Discord time to update presence
        except Exception as e:
            print(f"Failed to update status on shutdown: {e}")
        await super().close()


# --- DISCORD BOT SETUP ---
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = GiveawayBot(command_prefix="!", intents=intents)

# Active giveaways store: message_id -> {"prize": str, "participants": set}
active_giveaways = {}

def parse_duration(time_str: str) -> int:
    match = re.match(r"^(\d+)([smhd])?$", time_str.lower().strip())
    if not match:
        return -1
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == 'm': return amount * 60
    elif unit == 'h': return amount * 3600
    elif unit == 'd': return amount * 86400
    else: return amount

class GiveawayView(discord.ui.View):
    def __init__(self, message_id: int, host_name: str):
        super().__init__(timeout=None)
        self.message_id = message_id
        self.host_name = host_name

    @discord.ui.button(label="Join 🎉 (0)", style=discord.ButtonStyle.success, custom_id="join_giveaway_btn")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        msg_id = interaction.message.id
        user_id = interaction.user.id
        
        if msg_id not in active_giveaways:
            await interaction.followup.send("This giveaway has ended or reset!", ephemeral=True)
            return
            
        participants = active_giveaways[msg_id]["participants"]
        if user_id in participants:
            await interaction.followup.send("You are already in this giveaway!", ephemeral=True)
            return

        participants.add(user_id)
        button.label = f"Join 🎉 ({len(participants)})"
        
        embed = interaction.message.embeds[0]
        embed.set_footer(text=f"GiveawaySomething • {len(participants)} Participants • Hosted by {self.host_name}", icon_url="https://cdn-icons-png.flaticon.com/512/3135/3135715.png")
        
        try:
            await interaction.message.edit(embed=embed, view=self)
        except Exception:
            pass
            
        await interaction.followup.send("🎉 You have successfully joined the giveaway!", ephemeral=True)

    @discord.ui.button(label="Leave ✖️", style=discord.ButtonStyle.danger, custom_id="leave_giveaway_btn")
    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        msg_id = interaction.message.id
        user_id = interaction.user.id
        
        if msg_id not in active_giveaways:
            await interaction.followup.send("This giveaway has ended or reset!", ephemeral=True)
            return
            
        participants = active_giveaways[msg_id]["participants"]
        if user_id not in participants:
            await interaction.followup.send("You haven't joined this giveaway yet!", ephemeral=True)
            return

        participants.remove(user_id)
        self.children[0].label = f"Join 🎉 ({len(participants)})"
        
        embed = interaction.message.embeds[0]
        embed.set_footer(text=f"GiveawaySomething • {len(participants)} Participants • Hosted by {self.host_name}", icon_url="https://cdn-icons-png.flaticon.com/512/3135/3135715.png")
        
        try:
            await interaction.message.edit(embed=embed, view=self)
        except Exception:
            pass
            
        await interaction.followup.send("You have left the giveaway.", ephemeral=True)


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

        embed = discord.Embed(title="⚡ DROP CLAIMED! ⚡", description=f"**Prize:** {self.prize}\n**Winner:** {winner.mention}\n**Hosted by:** {self.host.mention}", color=discord.Color.gold(), timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.set_footer(text=f"GiveawaySomething • Hosted by {self.host.name}")

        await interaction.message.edit(embed=embed, view=self)
        await interaction.response.send_message(f"🎉 Congratulations {winner.mention}! You claimed the drop for **{self.prize}**!")

        try:
            await winner.send(f"⚡ Congratulations! You claimed the drop for **{self.prize}** in **{interaction.guild.name}**!")
        except discord.Forbidden:
            pass
        if self.host.id != winner.id:
            try:
                await self.host.send(f"⚡ Your drop for **{self.prize}** in **{interaction.guild.name}** was successfully claimed by {winner.mention}!")
            except discord.Forbidden:
                pass


# --- SELECT MENU FOR PARTICIPANTS ---
class GiveawaySelect(discord.ui.Select):
    def __init__(self, giveaways: dict):
        options = []
        for msg_id, data in giveaways.items():
            prize_name = data["prize"]
            count = len(data["participants"])
            label = f"{prize_name[:80]} ({count} participants)"
            options.append(discord.SelectOption(label=label, value=str(msg_id)))
        
        super().__init__(placeholder="Select a giveaway to view participants...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        msg_id = int(self.values[0])
        if msg_id not in active_giveaways:
            await interaction.followup.send("❌ This giveaway has ended or does not exist!", ephemeral=True)
            return

        data = active_giveaways[msg_id]
        participants = data["participants"]
        prize = data["prize"]

        if not participants:
            await interaction.followup.send(f"There are currently no registered participants for **{prize}**.", ephemeral=True)
            return

        names_list = []
        for u_id in participants:
            member = interaction.guild.get_member(u_id)
            if member:
                names_list.append(f"• {member.display_name} (`{member.name}`)")
            else:
                names_list.append(f"• User ID `{u_id}`")

        users_str = "\n".join(names_list)
        if len(users_str) > 4000:
            users_str = users_str[:3900] + "\n... (too many participants to display completely)"

        embed = discord.Embed(
            title=f"📋 Giveaway Participants: {prize}",
            description=f"**Total Participants:** {len(participants)}\n\n{users_str}",
            color=discord.Color.blue()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


class GiveawaySelectView(discord.ui.View):
    def __init__(self, giveaways: dict):
        super().__init__(timeout=60)
        self.add_item(GiveawaySelect(giveaways))


@bot.event
async def on_ready():
    try:
        # Global command sync across all servers where the bot is invited
        await bot.tree.sync()
        
        # Reset presence status to ONLINE when update completes
        await bot.change_presence(
            status=discord.Status.online,
            activity=discord.CustomActivity(name="🎉 Giveaways active!")
        )
        print(f"Logged in as {bot.user.name} - Global slash commands synced successfully!")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    print(f"Error in command: {error}")
    error_msg = f"❌ An error occurred: {error}"
    if not interaction.response.is_done():
        await interaction.response.send_message(error_msg, ephemeral=True)
    else:
        await interaction.followup.send(error_msg, ephemeral=True)


@bot.tree.command(name="giveaway", description="Start a timed giveaway! (Admin Only)")
@app_commands.describe(duration="Time format: 30s, 10m, 2h, 1d", prize="What are you giving away?", winners="Number of winners (default: 1)")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def start_giveaway(interaction: discord.Interaction, duration: str, prize: str, winners: int = 1):
    await interaction.response.defer(ephemeral=True)

    seconds = parse_duration(duration)
    if seconds <= 0:
        await interaction.followup.send("❌ Invalid duration format! Use formats like `30s`, `10m`, `2h`, or `1d`.", ephemeral=True)
        return

    end_time = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)
    timestamp_format = f"<t:{int(end_time.timestamp())}:R>"

    embed = discord.Embed(
        title=f"🎉 {prize} 🎉",
        description=f"Click the **Join 🎉** button below to enter!\n\n⏱️ **Ends:** {timestamp_format}\n👑 **Hosted by:** {interaction.user.mention}\n🏆 **Winners:** {winners}",
        color=discord.Color.green(),
        timestamp=end_time
    )
    embed.set_thumbnail(url="https://cdn-icons-png.flaticon.com/512/3135/3135715.png")
    embed.set_footer(text=f"GiveawaySomething • 0 Participants • Hosted by {interaction.user.name}", icon_url="https://cdn-icons-png.flaticon.com/512/3135/3135715.png")

    try:
        msg = await interaction.channel.send(embed=embed)
        await interaction.followup.send("✅ Giveaway created successfully!", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send("❌ Error: I don't have permission to send messages in this channel.", ephemeral=True)
        return
    
    view = GiveawayView(msg.id, interaction.user.name)
    await msg.edit(view=view)
    active_giveaways[msg.id] = {"prize": prize, "participants": set()}

    await asyncio.sleep(seconds)

    giveaway_data = active_giveaways.pop(msg.id, None)
    participants_list = list(giveaway_data["participants"]) if giveaway_data else []

    if not participants_list:
        ended_embed = discord.Embed(title=f"🎉 {prize} (ENDED) 🎉", description=f"**Winner:** No participants registered.\n**Hosted by:** {interaction.user.mention}", color=discord.Color.red())
        ended_embed.set_footer(text=f"GiveawaySomething • Ended • Hosted by {interaction.user.name}")
        await msg.edit(embed=ended_embed, view=None)
        await interaction.channel.send(f"The giveaway for **{prize}** ended, but nobody joined! 😢")
        try:
            await interaction.user.send(f"📢 Your giveaway for **{prize}** in **{interaction.guild.name}** ended with no participants.")
        except discord.Forbidden:
            pass
    else:
        num_winners = min(winners, len(participants_list))
        winner_ids = random.sample(participants_list, num_winners)
        winner_mentions = ", ".join([f"<@{w_id}>" for w_id in winner_ids])

        ended_embed = discord.Embed(title=f"🎉 {prize} (ENDED) 🎉", description=f"🏆 **Winner(s):** {winner_mentions}\n👑 **Hosted by:** {interaction.user.mention}", color=discord.Color.gold())
        ended_embed.set_footer(text=f"GiveawaySomething • Ended • Hosted by {interaction.user.name}")
        await msg.edit(embed=ended_embed, view=None)
        await interaction.channel.send(f"🎉 Congratulations {winner_mentions}! You won **{prize}**! 🎁")

        for w_id in winner_ids:
            member = interaction.guild.get_member(w_id)
            if member:
                try:
                    await member.send(f"🎉 Congratulations! You won the giveaway for **{prize}** in **{interaction.guild.name}**!")
                except discord.Forbidden:
                    pass
        try:
            await interaction.user.send(f"📢 Your giveaway for **{prize}** in **{interaction.guild.name}** has ended!\n🏆 Winner(s): {winner_mentions}")
        except discord.Forbidden:
            pass


@bot.tree.command(name="drop", description="Start a drop giveaway! (Admin Only)")
@app_commands.describe(prize="What is the drop prize?")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def start_drop(interaction: discord.Interaction, prize: str):
    await interaction.response.defer(ephemeral=True)
    
    embed = discord.Embed(title="⚡ QUICK DROP! ⚡", description=f"**Prize:** {prize}\n**Hosted by:** {interaction.user.mention}\n\nFirst person to click **CLAIM DROP!** wins!", color=discord.Color.blue())
    embed.set_footer(text=f"GiveawaySomething • Fast Drop • Hosted by {interaction.user.name}")

    view = DropView(prize, interaction.user)
    try:
        await interaction.channel.send(embed=embed, view=view)
        await interaction.followup.send("✅ Drop created successfully!", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send("❌ Error: I don't have permission to send messages in this channel.", ephemeral=True)


@bot.tree.command(name="participants", description="See all participants of an active giveaway via menu (Admin Only)")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def list_participants(interaction: discord.Interaction):
    if not active_giveaways:
        await interaction.response.send_message("❌ There are currently no active giveaways in this server!", ephemeral=True)
        return

    view = GiveawaySelectView(active_giveaways)
    await interaction.response.send_message("Select a giveaway from the menu below to view its participants:", view=view, ephemeral=True)


@bot.tree.command(name="remove_participant", description="Remove a user from an active giveaway (Admin Only)")
@app_commands.describe(user="The user to remove")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def remove_participant(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.defer(ephemeral=True)
    
    if not active_giveaways:
        await interaction.followup.send("❌ There are currently no active giveaways in this server!", ephemeral=True)
        return

    found = False
    for msg_id, data in active_giveaways.items():
        if user.id in data["participants"]:
            data["participants"].remove(user.id)
            found = True
            try:
                msg = await interaction.channel.fetch_message(msg_id)
                embed = msg.embeds[0]
                embed.set_footer(text=f"GiveawaySomething • {len(data['participants'])} Participants • Hosted by {interaction.user.name}", icon_url="https://cdn-icons-png.flaticon.com/512/3135/3135715.png")
                view = GiveawayView(msg_id, interaction.user.name)
                view.children[0].label = f"Join 🎉 ({len(data['participants'])})"
                await msg.edit(embed=embed, view=view)
            except Exception:
                pass
            break

    if found:
        await interaction.followup.send(f"✅ Successfully removed {user.mention} from the giveaway!", ephemeral=True)
    else:
        await interaction.followup.send(f"❌ {user.mention} is not registered in any active giveaway.", ephemeral=True)


async def main():
    TOKEN = os.getenv("DISCORD_TOKEN")
    if not TOKEN:
        print("Error: DISCORD_TOKEN environment variable is missing!")
        return

    await start_web_server()
    await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
