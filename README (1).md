# Free internship alerts

Checks Greenhouse, Lever and Internshala every hour, keeps postings from the last 48 hours, scores them against your keywords, and sends matches to Telegram.

## Setup (about 15 minutes)

1. **Telegram bot.** You need two values: a bot token and your chat ID.
   - In Telegram, search for **@BotFather** and send `/newbot`. Pick a name and username. It replies with a token that looks like `123456:ABC-DEF...`. That's `TELEGRAM_TOKEN`.
   - Open your new bot in Telegram and press Start (or send it "hi"). The bot can't message you until you do this.
   - In your browser, open this link with your token pasted in place of `<TOKEN>`: `https://api.telegram.org/bot<TOKEN>/getUpdates`
   - You'll see a block of text. Find `"chat":{"id":` followed by a number, like `987654321`. That number is `TELEGRAM_CHAT_ID`.
   - If the page shows an empty result, send another message to the bot and refresh.
2. **GitHub repo.** This is where the script lives and runs for free.
   - Sign in at github.com (make a free account if you don't have one).
   - Click **+** (top right) > **New repository**. Name it `job-alerts`, choose **Public**, and click Create.
   - Click **uploading an existing file**, then drag in `jobs_alert.py`, `companies.json`, `requirements.txt` and `README.md`. Commit.
   - The workflow file must sit at exactly `.github/workflows/alerts.yml`. Browser upload often drops hidden folders, so instead click **Add file > Create new file**, type `.github/workflows/alerts.yml` as the name (the slashes create the folders), paste the contents of `alerts.yml`, and commit.
3. **Secrets.** Repo > Settings > Secrets and variables > Actions > New repository secret:
   - `TELEGRAM_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - Optional: `GEMINI_API_KEY` (free key from Google AI Studio) and `RESUME_SUMMARY` (a short paragraph of your skills and projects). Both are needed for the Gemini fit check. Never put your resume in the repo itself, since it's public.
4. **Run it.** Actions tab > job-alerts > Run workflow. Check the log for "fetched N postings" and the Internshala card counts.

## Edit these

- `companies.json`: add companies. For Greenhouse, the slug is in `boards.greenhouse.io/<slug>`. For Lever, it's in `jobs.lever.co/<slug>`. Wrong slugs are skipped and logged.
- `KEYWORDS` and `MIN_KEYWORD_SCORE` in `jobs_alert.py`: tune until the alerts feel right.
- `INTERNSHALA_PAGES`: open each URL in your browser first to confirm it loads.

## Tailoring prompt (paste into claude.ai with the JD)

> Here is my master resume and a job description. Rewrite my bullets and reorder my projects for this role. Mirror the JD's wording only where it is true for me. Do not add any skill, tool, or claim that is not already in my resume. Keep it to one page and tell me what you changed.

## Notes

- The hourly cron can run late. If alerts stop entirely, check the Actions tab for failures.
- If Internshala shows 0 cards in the log, its HTML changed and the selectors in `fetch_internshala` need updating.
