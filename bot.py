import discord
from discord.ext import commands, tasks
from discord import app_commands
from aiohttp import web
import asyncio
import random
import datetime
import os
import re
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument

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


# --- MONGODB SETUP ---
MONGO_URI = os.getenv("MONGO_URI")
db_client = AsyncIOMotorClient(MONGO_URI)
db = db_client["giveaway_database"]
giveaways_collection = db["giveaways"]


# --- DISCORD BOT CLASS WITH GRACEFUL SHUTDOWN ---
class GiveawayBot(commands.Bot):
    async def setup_hook(self):
        self.cleanup_missed_giveaways.start()

    async def close(self):
        print("Render is updating/restarting! Updating bot status before shutdown...")
        try:
            await self.change_presence(
                status=discord.Status.dnd,
                activity=discord.CustomActivity(name="⏳ Server update in progress, please wait...")
            )
            await asyncio.sleep(2)
        except Exception as e:
            print(f"Failed to update status on shutdown: {e}")
        await super().close()

    @tasks.loop(minutes=1)
    async def cleanup_missed_giveaways(self):
        """Catches any giveaways that ended while the bot was restarting"""
        now = datetime.datetime.now(datetime.timezone.utc).timestamp()
        cursor = giveaways_collection.find({"active": True, "end_time": {"$lte": now}})
        async for giveaway in cursor:
            await process_giveaway_end(giveaway["message_id"], self)

    @cleanup_missed_giveaways.before_loop
    async def before_cleanup(self):
        await self.wait_until_ready()


# --- DISCORD BOT SETUP ---
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = GiveawayBot(command_prefix="!", intents=intents)

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

# --- CORE GIVEAWAY END LOGIC ---
async def process_giveaway_end(message_id: int, bot_instance: commands.Bot, force_ended_by=None):
    giveaway_data = await giveaways_collection.find_one_and_update(
        {"message_id": message_id, "active": True},
        {"$set": {"active": False}},
        return_document=ReturnDocument.BEFORE
    )
    
    if not giveaway_data:
        return False

    prize = giveaway_data["prize"]
    winners_count = giveaway_data["winners_count"]
    participants_list = giveaway_data.get("participants", [])
    channel_id = giveaway_data["channel_id"]
    host_id = giveaway_data["host_id"]
    host_name = giveaway_data["host_name"]
    
    channel = bot_instance.get_channel(channel_id)
    if not channel:
        return False
        
    try:
        msg = await channel.fetch_message(message_id)
    except discord.NotFound:
        return False

    end_title = f"🎉 {prize} (FORCE ENDED) 🎉" if force_ended_by else f"🎉 {prize} (ENDED) 🎉"

    if not participants_list:
        ended_embed = discord.Embed(title=end_title, description=f"**Winner:** No participants registered.\n**Hosted by:** <@{host_id}>", color=discord.Color.red())
        ended_embed.set_footer(text=f"GiveawaySomething • Ended • Hosted by {host_name}")
        await msg.edit(embed=ended_embed, view=None)
        await channel.send(f"The giveaway for **{prize}** ended, but nobody joined! 😢")
    else:
        num_winners = min(winners_count, len(participants_list))
        winner_ids = random.sample(participants_list, num_winners)
        winner_mentions = ", ".join([f"<@{w_id}>" for w_id in winner_ids])

        ended_embed = discord.Embed(title=end_title, description=f"🏆 **Winner(s):** {winner_mentions}\n👑 **Hosted by:** <@{host_id}>", color=discord.Color.gold())
        ended_embed.set_footer(text=f"GiveawaySomething • Ended • Hosted by {host_name}")
        await msg.edit(embed=ended_embed, view=None)
        
        announcement = f"🎉 Congratulations {winner_mentions}! You won **{prize}**! 🎁"
        if force_ended_by:
            announcement += f"\n*(Giveaway was force-ended early by {force_ended_by.mention})*"
        await channel.send(announcement)

    return True


# --- VIEWS & COMPONENTS ---
class GiveawayView(discord.ui.View):
    def __init__(self, message_id: int, host_name: str):
        super().__init__(timeout=None)
        self.message_id = message_id
        self.host_name = host_name

    @discord.ui.button(label="Join 🎉 (0)", style=discord.ButtonStyle.success, custom_id="join_giveaway_btn")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        user_id = interaction.user.id
        
        giveaway = await giveaways_collection.find_one({"message_id": self.message_id, "active": True})
        if not giveaway:
            await interaction.followup.send("This giveaway has ended or reset!", ephemeral=True)
            return
            
        if user_id in giveaway.get("participants", []):
            await interaction.followup.send("You are already in this giveaway!", ephemeral=True)
            return

        updated_giveaway = await giveaways_collection.find_one_and_update(
            {"message_id": self.message_id, "active": True},
            {"$addToSet": {"participants": user_id}},
            return_document=ReturnDocument.AFTER
        )
        
        if not updated_giveaway:
            await interaction.followup.send("This giveaway has ended!", ephemeral=True)
            return

        participants_count = len(updated_giveaway["participants"])
        button.label = f"Join 🎉 ({participants_count})"
        
        embed = interaction.message.embeds[0]
        embed.set_footer(text=f"GiveawaySomething • {participants_count} Participants • Hosted by {self.host_name}", icon_url="https://cdn-icons-png.flaticon.com/512/3135/3135715.png")
        
        try:
            await interaction.message.edit(embed=embed, view=self)
        except Exception:
            pass
            
        await interaction.followup.send("🎉 You have successfully joined the giveaway!", ephemeral=True)

    @discord.ui.button(label="Leave ✖️", style=discord.ButtonStyle.danger, custom_id="leave_giveaway_btn")
    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        user_id = interaction.user.id
        
        giveaway = await giveaways_collection.find_one({"message_id": self.message_id, "active": True})
        if not giveaway:
            await interaction.followup.send("This giveaway has ended or reset!", ephemeral=True)
            return
            
        if user_id not in giveaway.get("participants", []):
            await interaction.followup.send("You haven't joined this giveaway yet!", ephemeral=True)
            return

        updated_giveaway = await giveaways_collection.find_one_and_update(
            {"message_id": self.message_id, "active": True},
            {"$pull": {"participants": user_id}},
            return_document=ReturnDocument.AFTER
        )

        participants_count = len(updated_giveaway["participants"]) if updated_giveaway else 0
        self.children[0].label = f"Join 🎉 ({participants_count})"
        
        embed = interaction.message.embeds[0]
        embed.set_footer(text=f"GiveawaySomething • {participants_count} Participants • Hosted by {self.host_name}", icon_url="https://cdn-icons-png.flaticon.com/512/3135/3135715.png")
        
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


# --- SELECT MENUS ---
class GiveawaySelect(discord.ui.Select):
    def __init__(self, giveaways_list):
        options = []
        for g in giveaways_list:
            prize_name = g["prize"]
            count = len(g.get("participants", []))
            label = f"{prize_name[:80]} ({count} participants)"
            options.append(discord.SelectOption(label=label, value=str(g["message_id"])))
        
        super().__init__(placeholder="Select a giveaway to view participants...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        msg_id = int(self.values[0])
        
        giveaway = await giveaways_collection.find_one({"message_id": msg_id})
        if not giveaway:
            await interaction.followup.send("❌ This giveaway does not exist!", ephemeral=True)
            return

        participants = giveaway.get("participants", [])
        prize = giveaway["prize"]

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
            title=f"📋 Participants: {prize}",
            description=f"**Total Participants:** {len(participants)}\n\n{users_str}",
            color=discord.Color.blue()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


class ForceEndSelect(discord.ui.Select):
    def __init__(self, giveaways_list):
        options = []
        for g in giveaways_list:
            prize_name = g["prize"]
            label = f"{prize_name[:90]}"
            options.append(discord.SelectOption(label=label, value=str(g["message_id"])))
        
        super().__init__(placeholder="Select a giveaway to end IMMEDIATELY...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        msg_id = int(self.values[0])
        
        success = await process_giveaway_end(msg_id, bot, force_ended_by=interaction.user)
        if success:
            await interaction.followup.send("✅ Giveaway has been force-ended successfully!", ephemeral=True)
        else:
            await interaction.followup.send("❌ Failed to end giveaway. It might have already ended.", ephemeral=True)


class DynamicSelectView(discord.ui.View):
    def __init__(self, select_component):
        super().__init__(timeout=60)
        self.add_item(select_component)


# --- BOT EVENTS ---
@bot.event
async def on_ready():
    try:
        await bot.tree.sync()
        await bot.change_presence(
            status=discord.Status.online,
            activity=discord.CustomActivity(name="🎉 Giveaways active!")
        )
        print(f"Logged in as {bot.user.name} - Global slash commands synced successfully!")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    error_msg = f"❌ An error occurred: {error}"
    if not interaction.response.is_done():
        await interaction.response.send_message(error_msg, ephemeral=True)
    else:
        await interaction.followup.send(error_msg, ephemeral=True)


# --- COMMANDS ---
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
    
    await giveaways_collection.insert_one({
        "message_id": msg.id,
        "channel_id": msg.channel.id,
        "guild_id": interaction.guild.id,
        "prize": prize,
        "winners_count": winners,
        "end_time": end_time.timestamp(),
        "participants": [],
        "active": True,
        "host_id": interaction.user.id,
        "host_name": interaction.user.name
    })

    # Wait for the duration
    await asyncio.sleep(seconds)
    
    # Process the end (will automatically do nothing if it was force-ended)
    await process_giveaway_end(msg.id, bot)


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
    cursor = giveaways_collection.find({"active": True, "guild_id": interaction.guild.id})
    active_list = await cursor.to_list(length=25)
    
    if not active_list:
        await interaction.response.send_message("❌ There are currently no active giveaways in this server!", ephemeral=True)
        return

    view = DynamicSelectView(GiveawaySelect(active_list))
    await interaction.response.send_message("Select a giveaway from the menu below to view its participants:", view=view, ephemeral=True)


@bot.tree.command(name="force-end", description="Force end an active giveaway immediately via menu (Admin Only)")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def force_end(interaction: discord.Interaction):
    cursor = giveaways_collection.find({"active": True, "guild_id": interaction.guild.id})
    active_list = await cursor.to_list(length=25)
    
    if not active_list:
        await interaction.response.send_message("❌ There are currently no active giveaways to end in this server!", ephemeral=True)
        return

    view = DynamicSelectView(ForceEndSelect(active_list))
    await interaction.response.send_message("⚠️ Select a giveaway from the menu below to **end it immediately**:", view=view, ephemeral=True)


@bot.tree.command(name="remove_participant", description="Remove a user from an active giveaway (Admin Only)")
@app_commands.describe(user="The user to remove")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def remove_participant(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.defer(ephemeral=True)
    
    updated = await giveaways_collection.find_one_and_update(
        {"active": True, "guild_id": interaction.guild.id, "participants": user.id},
        {"$pull": {"participants": user.id}},
        return_document=ReturnDocument.AFTER
    )
    
    if updated:
        try:
            channel = bot.get_channel(updated["channel_id"])
            msg = await channel.fetch_message(updated["message_id"])
            embed = msg.embeds[0]
            embed.set_footer(text=f"GiveawaySomething • {len(updated['participants'])} Participants • Hosted by {updated['host_name']}", icon_url="https://cdn-icons-png.flaticon.com/512/3135/3135715.png")
            view = GiveawayView(updated["message_id"], updated["host_name"])
            view.children[0].label = f"Join 🎉 ({len(updated['participants'])})"
            await msg.edit(embed=embed, view=view)
        except Exception:
            pass
        
        await interaction.followup.send(f"✅ Successfully removed {user.mention} from the giveaway: **{updated['prize']}**", ephemeral=True)
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
