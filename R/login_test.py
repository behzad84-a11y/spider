from telethon import TelegramClient

api_id = 24545640   # <-- API ID
api_hash = "0b2f9f8a07d5a53a4cd78fa571b44c14"  # <-- API HASH
phone = "+31659115161"  # <-- شماره با کد کشور

client = TelegramClient("user_session", api_id, api_hash)

async def main():
    await client.start(phone=phone)
    me = await client.get_me()
    print("Logged in as:", me.username or me.first_name, me.id)

with client:
    client.loop.run_until_complete(main())