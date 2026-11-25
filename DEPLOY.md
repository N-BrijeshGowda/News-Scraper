# Putting News Scraper on your CloudPanel server (Hostinger)

Menu names in CloudPanel change a little between versions. If something is not where this guide says,
look for the closest match, or ask your hosting support.

You need:
- The Hostinger VPS with CloudPanel installed, and the CloudPanel admin login.
- The domain name, with its DNS pointing at the server's IP address (an "A record").
- SSH access to the server (CloudPanel shows the details, or Hostinger's panel does).
- Recommended server size: 2 GB of memory is the minimum, 4 GB is comfortable for video processing.

---

## Step 1. Create the site in CloudPanel
1. CloudPanel > **Sites** > **Add Site** > **Create a Python Site**.
2. Domain name: your domain. Python version: **3.12** (or the newest offered). **App Port: 8000**.
3. Choose a site user name and password, and save them. Click **Create**.

## Step 2. HTTPS
Site > **SSL/TLS** > **New Let's Encrypt Certificate** > Create. (The domain must already point at the server.)

## Step 3. Upload the project
1. On your PC, zip the project folder **without** these files and folders (they hold secrets or live data):
   `.env`, `credentials.json`, `secret.key`, `username wodo.txt`, `.news-scraper.env.txt`, `news.db`,
   `media`, `uploads`, `__pycache__`, `.venv`.
2. In CloudPanel > the site > **File Manager** (or SFTP with the site user), open `htdocs/YOUR-DOMAIN/`,
   upload the zip and extract it so `app.py` sits directly in that folder.

## Step 4. Install it (SSH as the site user)
```bash
ssh SITEUSER@YOUR-SERVER-IP
cd ~/htdocs/YOUR-DOMAIN
sed -i 's/\r$//' start.sh deploy/*.sh      # fixes Windows line endings
chmod +x start.sh deploy/backup.sh
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```
This takes a few minutes.

## Step 5. Settings file with the secrets
Create the file `~/htdocs/YOUR-DOMAIN/.env` on the server (for example with `nano .env`):
```
APP_USERNAME=team
APP_PASSWORD=choose-a-long-password-here
SECRET_KEY=paste-64-random-characters-here
ANTHROPIC_API_KEY=sk-ant-your-key
SCHEDULE_TZ=Asia/Kolkata
RUN_AT=07:00
```
- Make a random `SECRET_KEY` with: `python3 -c "import secrets; print(secrets.token_hex(32))"`
- Set `SCHEDULE_TZ` to your own time zone name (for example `Europe/London`). Otherwise the daily 07:00 run
  uses the server's clock.
- Lock the file: `chmod 600 .env`
- Do **not** set `ALLOW_YOUTUBE_DOWNLOAD` on a server (it breaks YouTube's terms and can get the server blocked).

## Step 6. Keep it running (needs the server's root login)
```bash
sudo cp ~/htdocs/YOUR-DOMAIN/deploy/news-scraper.service /etc/systemd/system/news-scraper.service
sudo nano /etc/systemd/system/news-scraper.service     # replace SITEUSER and YOUR-DOMAIN (4 places)
sudo systemctl daemon-reload
sudo systemctl enable --now news-scraper
sudo systemctl status news-scraper                      # should say "active (running)"
```
It now starts on boot and restarts itself if it crashes. To watch its messages: `sudo journalctl -u news-scraper -f`

## Step 7. Allow big video uploads
CloudPanel > the site > **Vhost**. Inside the main `server { ... }` block add:
```
client_max_body_size 2048M;
proxy_read_timeout 600s;
proxy_send_timeout 600s;
```
Save. Without this, uploading a video file fails with a "413" error.

## Step 8. Try it
- Open `https://YOUR-DOMAIN`. You should see the sign-in page. Sign in with `APP_USERNAME` and `APP_PASSWORD`.
- `https://YOUR-DOMAIN/healthz` should show `{"ok":true}`.
- Add a website and press **Run now**.

## Step 9. Daily backups
CloudPanel > the site > **Cron Jobs** > add a job, every day at 03:00:
```
bash /home/SITEUSER/htdocs/YOUR-DOMAIN/deploy/backup.sh
```
Backups go to `~/backups` and the last 14 days are kept. Also turn on Hostinger's server snapshots if your plan has them.

## Updating later
1. Upload the changed files (never overwrite `news.db`, `media`, or `.env` on the server).
2. If `requirements.txt` changed: `cd ~/htdocs/YOUR-DOMAIN && .venv/bin/pip install -r requirements.txt`
3. `sudo systemctl restart news-scraper`

## Moving your current data from the PC (optional)
Copy `news.db` and the `media` folder from your PC into `~/htdocs/YOUR-DOMAIN/` on the server while the
service is stopped (`sudo systemctl stop news-scraper`), then start it again.

---

## If something goes wrong
| Problem | Likely cause and fix |
|---|---|
| **502 Bad Gateway** | The app is not running or the port differs. Check `sudo systemctl status news-scraper` and that CloudPanel's App Port is 8000. |
| **Can't sign in, or kicked out** | Make sure the site is opened with `https://`. If you must test over plain `http`, remove `export COOKIE_SECURE=1` from `start.sh`. |
| **Video upload fails (413)** | Step 7 was not saved. |
| **Transcripts fail on the server but worked on your PC** | YouTube often blocks data-centre addresses. A paid proxy service can fix this; ask me to add proxy support. |
| **A website returns no articles only on the server** | Some sites block data-centre addresses too. |
| **Daily run happens at the wrong hour** | Set `SCHEDULE_TZ` in `.env` and restart the service. |
| **App won't start** | `sudo journalctl -u news-scraper -n 50` shows the error. |

## Safety checklist
- Use a long, unique `APP_PASSWORD`, and share it only with your team.
- Delete the `.env` file from your OneDrive project folder once the server works, and replace the Anthropic key
  in the Console if it was ever stored in OneDrive.
- Keep only ports 80 and 443 open to the internet (CloudPanel's firewall page).
- Keep the server's software updated.
