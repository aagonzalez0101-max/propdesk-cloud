# propdesk-cloud
Always-on PropDesk alerts (promos, line moves on watched players, sharp value) via GitHub Actions -> Discord/ntfy.
Secrets: DISCORD_WEBHOOK, NTFY_TOPIC. state/watch.json = [{"player": "...", "league": "NHL"}, ...] (open bets + card legs), written by the card builder: those leagues are checked every 10 min, all other leagues every 30 min.
