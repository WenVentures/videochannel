# Video Pipeline — Setup

This pipeline runs on your Mac, builds a 30-second vertical reel from Pexels footage with a Samantha voiceover and burned-in captions, and (Phase 2) posts it automatically to Instagram. YouTube Shorts and TikTok integrations come in later phases.

Everything is free-tier. No credit card is required for any service below.

---

## 1. Install files on your Mac

Download the four files I delivered into `~/Downloads`:

```
pipeline.py
scripts.json
SETUP.md            (this file)
com.wen.videopipeline.plist
```

Open **Terminal** and create the project directory:

```
mkdir -p ~/src/WenVideoChannel
mv ~/Downloads/pipeline.py ~/src/WenVideoChannel/pipeline.py
mv ~/Downloads/scripts.json ~/src/WenVideoChannel/scripts.json
mv ~/Downloads/youtube_auth.py ~/src/WenVideoChannel/youtube_auth.py
chmod +x ~/src/WenVideoChannel/pipeline.py ~/src/WenVideoChannel/youtube_auth.py
```

Install ffmpeg if you don't have it:

```
which ffmpeg || brew install ffmpeg
```

Create an empty `.env`:

```
touch ~/src/WenVideoChannel/.env
chmod 600 ~/src/WenVideoChannel/.env
```

---

## 2. Pexels API key (required — Phase 1)

Used for sourcing stock footage.

1. Go to https://www.pexels.com/api/
2. Click "Get Started", sign up with email. No card.
3. On the dashboard, copy your API key.
4. Add it to `~/src/WenVideoChannel/.env`:

```
PEXELS_KEY=paste-your-key-here
```

You can now run a dry run:

```
python3 ~/src/WenVideoChannel/pipeline.py --dry-run
```

This builds one video from a random script in the bank and opens it in QuickTime. It does not post anywhere. Run this a few times until you like what comes out.

---

## 3. Cloudinary — video hosting (required for Instagram posting)

Instagram's API doesn't accept direct uploads — it pulls the video from a public URL. Cloudinary's free tier (25 GB/mo, no card) is the cleanest host.

1. Go to https://cloudinary.com/users/register_free
2. Sign up with email.
3. On the dashboard, note your **Cloud name** (top-left).
4. Go to Settings → Upload → Upload presets → "Add upload preset".
   - Name: `videopipeline_unsigned`
   - Signing mode: **Unsigned**
   - Resource type: **Video**
   - Save.
5. Add to `~/src/WenVideoChannel/.env`:

```
CLOUDINARY_CLOUD_NAME=your-cloud-name
CLOUDINARY_UPLOAD_PRESET=videopipeline_unsigned
```

---

## 4. Instagram Graph API (required for posting)

This is the big one. Meta requires the Instagram account to be a **Business or Creator** account, and linked to a **Facebook Page**. Both are free.

### 4.1 Create the Instagram account
Open Instagram, create the new account, then in the app go to Settings → Account type and tools → **Switch to Professional account → Creator**. Pick any category (e.g. "Digital creator").

### 4.2 Create a Facebook Page
Go to https://www.facebook.com/pages/create/. Name it whatever you want (it doesn't have to be public-facing — it just has to exist).

### 4.3 Link Instagram to the Page
In the Instagram app: Settings → Account Center → Connected experiences → Accounts → link the Facebook Page.

### 4.4 Create a Meta Developer app
1. Go to https://developers.facebook.com/ and log in with the Facebook account that owns the Page.
2. Click **My Apps → Create App**.
3. App type: **Business**. Name: anything (e.g. "videopipeline").
4. In the new app, go to **Add Product** and add **Instagram Graph API**.

### 4.5 Generate a long-lived access token
This is the most finicky step. Short version:

1. Go to the **Graph API Explorer**: https://developers.facebook.com/tools/explorer/
2. Top-right, select your app.
3. Click "Generate Access Token" and approve these permissions:
   - `instagram_basic`
   - `instagram_content_publish`
   - `pages_show_list`
   - `pages_read_engagement`
   - `business_management`
4. You now have a short-lived (1-hour) token. Exchange it for a long-lived (60-day) token:

```
curl -G 'https://graph.facebook.com/v20.0/oauth/access_token' \
  -d 'grant_type=fb_exchange_token' \
  -d 'client_id=YOUR_APP_ID' \
  -d 'client_secret=YOUR_APP_SECRET' \
  -d "fb_exchange_token=THE_SHORT_TOKEN"
```

App ID and secret are on **App Settings → Basic** in the dev console. The response's `access_token` is the long-lived one.

5. Get your Instagram Business Account ID:

```
curl -G "https://graph.facebook.com/v20.0/me/accounts" \
  -d "access_token=LONG_LIVED_TOKEN"
```

That returns your pages. Take the page `id`, then:

```
curl -G "https://graph.facebook.com/v20.0/PAGE_ID" \
  -d "fields=instagram_business_account" \
  -d "access_token=LONG_LIVED_TOKEN"
```

The number under `instagram_business_account.id` is what you want.

6. Add to `~/src/WenVideoChannel/.env`:

```
IG_TOKEN=your-long-lived-token
IG_BUSINESS_ID=your-ig-business-account-id
```

**Note:** the long-lived token expires every 60 days. Set a reminder to refresh it — or we can add an auto-refresh step later.

---

## 5. First real post

Once the `.env` has all five values (`PEXELS_KEY`, `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_UPLOAD_PRESET`, `IG_TOKEN`, `IG_BUSINESS_ID`):

```
python3 ~/src/WenVideoChannel/pipeline.py
```

That will build a fresh video and publish it to your Instagram as a Reel. Watch the Terminal output — if something fails, the error message tells you which step.

Good practice: run `--dry-run` five or ten times first and look at the outputs. If you don't like a script, delete it from `scripts.json`. If you want to tweak a voiceover, edit it in place.

---

## 6. Scheduling — 2 posts/day automatically

Move the launchd plist into place and load it:

```
mv ~/Downloads/com.wen.videopipeline.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.wen.videopipeline.plist
```

That schedules two runs per day, at **9:00** and **17:30** local time. Stdout goes to `/tmp/videopipeline.out.log`, errors to `/tmp/videopipeline.err.log`.

### To change the schedule
Edit the plist's `StartCalendarInterval` dict entries, then:

```
launchctl unload ~/Library/LaunchAgents/com.wen.videopipeline.plist
launchctl load ~/Library/LaunchAgents/com.wen.videopipeline.plist
```

### To pause posting
```
launchctl unload ~/Library/LaunchAgents/com.wen.videopipeline.plist
```

### To uninstall entirely
```
launchctl unload ~/Library/LaunchAgents/com.wen.videopipeline.plist
rm ~/Library/LaunchAgents/com.wen.videopipeline.plist
rm -rf ~/src/WenVideoChannel
```

---

## 7. Day-to-day commands

```
# List scripts in the bank (✓ = already used this cycle)
python3 ~/src/WenVideoChannel/pipeline.py --list

# Force a specific script
python3 ~/src/WenVideoChannel/pipeline.py --script-id city-love-reflection

# Build without posting (for QA)
python3 ~/src/WenVideoChannel/pipeline.py --dry-run

# See what the scheduler posted
tail -n 100 /tmp/videopipeline.out.log
```

---

## 8. When the script bank runs low

The state file (`~/src/WenVideoChannel/state.json`) tracks which scripts have been used this cycle. After all 20 are used, the pipeline resets and rotates through them again — so nothing breaks if you never add more.

But to keep the account fresh, ask me in a future Cowork chat:

> "Add 20 more scripts to the bank, focused on [topic/mood/season]."

I'll generate a new batch and tell you where to paste them.

---

## 9. What Phase 2 will add

- YouTube Shorts auto-upload (needs Google OAuth one-time setup)
- TikTok once their Content Posting API approves the app (1–4 weeks)
- Performance tracking — pulls view/like/follow counts 24h after each post, writes to SQLite, lets the topic picker favour hook styles and topics that converted best
- Auto-refresh of the IG long-lived token before it expires

---

## 10. If something breaks

Run the pipeline manually with `--dry-run` first — it'll tell you which step failed. Common issues:

- **"Not enough clips for query X"** — Pexels doesn't have enough portrait HD matches. Edit that script's `pexels_query` in `scripts.json` to something broader.
- **"Cloudinary upload failed"** — check the cloud name and preset name in `.env` match exactly. Preset must be set to **Unsigned** and **Video**.
- **"IG container error" / "session expired"** — your IG_TOKEN expired (60-day limit). Regenerate it from section 4.5.
- **No video appears and no error** — check `/tmp/videopipeline.err.log`.
