#!/bin/bash

# Start Xvfb (virtual display server)
Xvfb :99 -screen 0 1920x1080x24 > /dev/null 2>&1 &
export DISPLAY=:99

# Run the bot
python bot.py