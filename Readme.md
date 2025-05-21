# Automated Udemy Coupon Bot

This bot automatically scrapes free Udemy course coupons from DiscUdemy and posts them to a Telegram channel.

## Features

- **Fully Automated**: Scrapes, processes, and posts without any manual intervention
- **Direct Pipeline**: No need for Google Sheets as an intermediary
- **Regular Updates**: Scrapes for new coupons hourly and posts every 10-15 minutes
- **Health Checking**: Built-in health endpoint for monitoring
- **Docker Ready**: Containerized for easy deployment

## How It Works

1. **Scraping**: The bot scrapes DiscUdemy at regular intervals using a headless Chrome browser
2. **Processing**: It extracts course slugs and coupon codes
3. **Enrichment**: For each coupon, it fetches course details from Udemy (title, image, description)
4. **Posting**: It posts formatted course cards to the configured Telegram channel

## Configuration

All configuration is handled in the `bot.py` file:

## Deployment on Render

This bot is optimized for deployment on Render's free tier:

1. Create a new Web Service
2. Connect your repository
3. Set Build Command: `pip install -r requirements.txt`
4. Set Start Command: `bash start.sh`
5. Add Environment Variables:
   - `PORT`: `10000`
   - `PYTHONUNBUFFERED`: `1`

## Local Development

To run locally with Docker:

```bash
docker-compose up --build
```

Or without Docker:

```bash
pip install -r requirements.txt
python bot.py
```

## Health Check

The bot exposes a health check endpoint at `/healthz` on port `10000` that returns a `200 OK` response when the bot is running.

## Files Overview

- `bot.py`: Main application file
- `discudemy_scraper.py`: Module for scraping DiscUdemy
- `requirements.txt`: Python dependencies
- `Dockerfile`: Container definition
- `docker-compose.yml`: Docker Compose configuration
- `start.sh`: Startup script for container environment