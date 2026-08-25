# # DexPaid Watcher

A personal Telegram bot. Paste a Solana contract address into the chat and
it watches DexScreener in the background, pinging you the moment "Enhanced
Token Info" (a.k.a. "Dex paid") is approved for that token.

Uses DexScreener's free public API — no paid API key needed.

## How it works

1. You send a message containing a Solana contract address.
2. The bot adds it to a watchlist (a small JSON file) and replies to confirm.
3. Every `POLL_INTERVAL_SECONDS` (default 60s), a background job checks
   every watched address against DexScreener's orders endpoint.
4. The moment a token's status is `approved`, you get a message and it's
   removed from the watchlist.
5. Tokens older than `STALE_AFTER_HOURS` (default 48h) are dropped
   automatically so dead ones don't sit there forever.

Only the chat ID you configure as `OWNER_CHAT_ID` can use the bot — anyone
else who finds it and messages it is ignored.

## 1. Create the bot on Telegram

1. Open a chat with [@BotFather](https://t.me/BotFather) on Telegram.
2. Send `/newbot` and follow the prompts.
3. Copy the token it gives you — this is `TELEGRAM_BOT_TOKEN`.

## 2. Deploy to Railway

1. Push this repo to GitHub.
2. On [railway.com](https://railway.com), click **New Project → Deploy from GitHub repo**
   and pick this repo.
3. Railway will detect the `Procfile` and run it as a worker automatically
   (no web port needed — this bot doesn't serve HTTP traffic).
4. Under the service's **Variables** tab, add:
   - `TELEGRAM_BOT_TOKEN` — from step 1
   - Leave `OWNER_CHAT_ID` unset for now
5. Deploy.

## 3. Lock the bot to yourself

1. Message your bot `/start` on Telegram.
2. It replies with your chat ID.
3. Add `OWNER_CHAT_ID` (that number) as a variable in Railway.
4. Redeploy. From now on, only your account can use the bot.

## 4. Use it

- Paste any Solana contract address into the chat → bot starts watching it.
- `/list` — see everything currently being watched, and for how long.
- `/stop <address>` — stop watching a specific token.

## Notes on persistence

The watchlist is stored in `watchlist.json` next to the script. Railway's
filesystem is ephemeral by default — a redeploy or restart will wipe it.
For a personal bot with short-lived watches (hours, not weeks) this is
usually fine. If you want it to survive restarts, add a
[Railway Volume](https://docs.railway.com/reference/volumes) mounted at the
project directory, or point `DATA_FILE` at a path inside that volume.

## Notes on rate limits

DexScreener's free orders endpoint allows 60 requests/minute. This bot
checks addresses one at a time with a 1-second gap between each, so even a
few hundred watched tokens stay comfortably under that limit.
