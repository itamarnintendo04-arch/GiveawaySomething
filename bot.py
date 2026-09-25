import os
import asyncio
import random
from datetime import datetime, timedelta, timezone
import discord
from discord.ext import commands, tasks
from aiohttp import web
from motor.motor_asyncio import AsyncIOMotorClient

# --- הגדרת שרת הדמה עבור Render ---
async def handle(request):
    return web.Response(text="Giveaway Bot is active and running!")

app = web.Application()
app.router.add_get("/", handle)

async def start_web_server():
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Dummy web server started on port {port}")

# --- הגדרת חיבור ל-MongoDB ---
MONGO_URI = os.getenv("MONGO_URI")
db_client = AsyncIOMotorClient(MONGO_URI)
db = db_client["giveaway_database"]
giveaways_collection = db["giveaways"]

# --- הגדרת הבוט של דיסקורד ---
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --- תצוגת הכפתורים של ההגרלה ---
class GiveawayView(discord.ui.View):
    def __init__(self, message_id: int):
        super().__init__(timeout=None)
        self.message_id = message_id

    @discord.ui.button(label="🎉 השתתף בהגרלה", style=discord.ButtonStyle.primary, custom_id="join_giveaway_button")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        giveaway = await giveaways_collection.find_one({"message_id": self.message_id})
        
        if not giveaway or not giveaway.get("active", False):
            await interaction.response.send_message("ההגרלה הזו כבר הסתיימה או לא קיימת.", ephemeral=True)
            return
            
        user_id = interaction.user.id
        if user_id in giveaway.get("participants", []):
            await interaction.response.send_message("אתה כבר משתתף בהגרלה הזו! בהצלחה! 🍀", ephemeral=True)
            return

        # הוספת המשתמש למסד הנתונים
        await giveaways_collection.update_one(
            {"message_id": self.message_id}, 
            {"$push": {"participants": user_id}}
        )
        await interaction.response.send_message("נכנסת להגרלה בהצלחה! 🎉", ephemeral=True)

# --- אירוע הדלקת הבוט ---
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    bot.add_view(GiveawayView(0)) # רישום תצוגת הכפתורים כדי שיעבדו גם אחרי ריסטארט
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
    
    # הפעלת לולאת הבדיקה של ההגרלות
    if not check_giveaways.is_running():
        check_giveaways.start()

# --- פקודות ההגרלה ---
@bot.tree.command(name="gstart", description="התחל הגרלה חדשה")
async def gstart(interaction: discord.Interaction, prize: str, duration_minutes: int, winners: int = 1):
    end_time = datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)
    
    embed = discord.Embed(
        title="🎉 הגרלה חדשה! 🎉", 
        description=f"**פרס:** {prize}\n**מספר זוכים:** {winners}\n**מסתיימת ב:** <t:{int(end_time.timestamp())}:R>", 
        color=discord.Color.gold()
    )
    embed.set_footer(text="לחץ על הכפתור למטה כדי להשתתף!")
    
    await interaction.response.send_message("ההגרלה מתחילה...", ephemeral=True)
    msg = await interaction.channel.send(embed=embed)
    
    view = GiveawayView(message_id=msg.id)
    await msg.edit(view=view)

    # שמירת ההגרלה ב-MongoDB
    await giveaways_collection.insert_one({
        "message_id": msg.id,
        "channel_id": msg.channel.id,
        "prize": prize,
        "winners_count": winners,
        "end_time": end_time.timestamp(),
        "participants": [],
        "active": True
    })

@bot.tree.command(name="gend", description="סיים הגרלה באופן ידני")
async def gend(interaction: discord.Interaction, message_id: str):
    try:
        msg_id = int(message_id)
    except ValueError:
        await interaction.response.send_message("נא להזין מספר מזהה (ID) תקין.", ephemeral=True)
        return
    
    giveaway = await giveaways_collection.find_one({"message_id": msg_id, "active": True})
    if not giveaway:
        await interaction.response.send_message("לא נמצאה הגרלה פעילה עם ה-ID הזה.", ephemeral=True)
        return
    
    await end_giveaway(giveaway)
    await interaction.response.send_message("ההגרלה הסתיימה בהצלחה.", ephemeral=True)

# --- פונקציות רקע לניהול הגרלות ---
@tasks.loop(seconds=30)
async def check_giveaways():
    now = datetime.now(timezone.utc).timestamp()
    cursor = giveaways_collection.find({"active": True, "end_time": {"$lte": now}})
    async for giveaway in cursor:
        await end_giveaway(giveaway)

async def end_giveaway(giveaway):
    msg_id = giveaway["message_id"]
    channel_id = giveaway["channel_id"]
    prize = giveaway["prize"]
    winners_count = giveaway["winners_count"]
    participants = giveaway.get("participants", [])

    # עדכון מסד הנתונים שההגרלה הסתיימה
    await giveaways_collection.update_one({"message_id": msg_id}, {"$set": {"active": False}})

    channel = bot.get_channel(channel_id)
    if not channel:
        return

    try:
        msg = await channel.fetch_message(msg_id)
        # הסרת הכפתור מתיבת ההודעה
        await msg.edit(view=None) 
    except Exception:
        pass

    if len(participants) == 0:
        await channel.send(f"ההגרלה על **{prize}** הסתיימה, אך אף אחד לא השתתף. 😢")
        return
    
    # בחירת זוכים
    actual_winners_count = min(winners_count, len(participants))
    winner_ids = random.sample(participants, actual_winners_count)
    winners_mentions = ", ".join([f"<@{uid}>" for uid in winner_ids])

    embed = discord.Embed(
        title="🎉 ההגרלה הסתיימה! 🎉", 
        description=f"**פרס:** {prize}\n**זוכים:** {winners_mentions}", 
        color=discord.Color.green()
    )
    await channel.send(content=f"מזל טוב {winners_mentions}! זכיתם ב-**{prize}**!", embed=embed)

# --- נקודת כניסה ראשית המריצה הכל במקביל ---
async def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("Error: DISCORD_TOKEN environment variable not set!")
        return

    # הפעלת שרת ה-Web בצד הרקע עבור Render
    await start_web_server()

    # הפעלת הבוט של דיסקורד
    async with bot:
        await bot.start(token)

if __name__ == "__main__":
    asyncio.run(main())
