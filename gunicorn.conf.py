"""Production web server settings (gunicorn, Linux only).

ONE worker on purpose: scrapes and video jobs run as background threads inside the app,
and the daily timer lives in the same process. Several workers would run everything twice.
Threads give enough parallelism for a small team.
Kept small on purpose to use as little memory as possible.
"""
import os

bind = f"127.0.0.1:{os.environ.get('PORT', '8000')}"   # CloudPanel's Nginx forwards to this port
workers = 1
threads = 4            # enough for a small team; fewer threads = less memory
timeout = 600            # large video uploads can take a while
graceful_timeout = 30
accesslog = "-"
errorlog = "-"
forwarded_allow_ips = "127.0.0.1"
