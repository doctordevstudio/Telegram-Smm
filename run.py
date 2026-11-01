import asyncio
import uvicorn
import config
from web_admin import app
from main_bot import bot, dp

async def run_bot():
    print("🤖 Starting Telegram Aiogram Bot...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    # Create the global asyncio loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # 1. Schedule Bot
    loop.create_task(run_bot())
    
    # 2. Setup FastAPI Uvicorn Server on Render's Port
    uvicorn_config = uvicorn.Config(
        app=app, 
        host="0.0.0.0", 
        port=config.PORT, 
        loop="asyncio"
    )
    server = uvicorn.Server(uvicorn_config)
    
    print(f"🌐 Starting FastAPI Admin Panel on port {config.PORT}...")
    
    # Run the server until process is killed
    loop.run_until_complete(server.serve())
