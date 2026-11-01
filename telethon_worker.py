import os
import glob
import asyncio
import random
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.functions.channels import JoinChannelRequest, GetFullChannelRequest
from telethon.tl.functions.messages import SendReactionRequest
from telethon.tl.functions.phone import JoinGroupCallRequest
from telethon.tl.types import ReactionEmoji
import config, database

MAX_CONCURRENT_TASKS = 3 # Kept low for Render free tier

async def get_clients_for_order(required_count):
    session_files = glob.glob(os.path.join(config.SESSION_DIR, "*.session"))
    api_creds = database.get_settings().get('api_credentials', [])
    
    if not session_files or not api_creds:
        return []

    selected = random.sample(session_files, min(len(session_files), required_count))
    clients = []
    for idx, path in enumerate(selected):
        cred = api_creds[idx % len(api_creds)]
        clients.append(TelegramClient(path, cred['api_id'], cred['api_hash']))
    return clients

async def execute_task(client, order_type, target, config_data):
    try:
        await client.connect()
        if not await client.is_user_authorized(): return False
        
        # 1. Join Channel
        await client(JoinChannelRequest(target))
        await asyncio.sleep(random.uniform(1, 3))

        # 2. View
        if order_type == "views":
            chat, msg_id = target.split("_")[0] if "_" in target else target, int(target.split("_")[1]) if "_" in target else 0
            if msg_id: await client.get_messages(chat, ids=[msg_id])

        # 3. Reaction
        elif order_type == "reactions":
            chat, msg_id = target.split("_")[0], int(target.split("_")[1])
            await client(SendReactionRequest(peer=chat, msg_id=msg_id, reaction=[ReactionEmoji(emoticon='❤️')]))

        # 4. Livestream (Bandwidth Optimized)
        elif order_type == "livestream":
            full = await client(GetFullChannelRequest(target))
            if full.full_chat.call:
                await client(JoinGroupCallRequest(call=full.full_chat.call, join_as=await client.get_input_entity(target), muted=True, video_stopped=True))
                await asyncio.sleep(120) # Stay in stream for 2 mins (adjust as needed)
                
        return True
    except FloodWaitError as e:
        print(f"Rate limit: {e.seconds}s on {client.session.filename}")
        return False
    except Exception as e:
        print(f"Error on {client.session.filename}: {e}")
        return False
    finally:
        await client.disconnect()

async def process_order(order_id, order_data):
    clients = await get_clients_for_order(order_data['count'])
    if not clients:
        database.update_order_status(order_id, "failed")
        return

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
    async def sem_task(client):
        async with semaphore: return await execute_task(client, order_data['type'], order_data['target'], order_data['config'])

    results = await asyncio.gather(*(sem_task(c) for c in clients))
    database.update_order_status(order_id, "completed" if any(results) else "failed")
