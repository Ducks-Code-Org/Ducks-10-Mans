# Getting Started (for New Developers)

This guide will cover everything you need to do to set up the codebase for local development with a test 10-mans discord bot (on windows).

## Requirements

- `python 3.12`
- A Discord Account + Server
- A [MongoDB Atlas](https://www.mongodb.com/try?tck=community_atlas_ct) Account

## Setup

- Head over to the [Discord Developer Portal](https://discord.com/developers/applications) and select "New Application"
  - Name it something useful
  - Invite this new bot to a discord server of your choice (for testing)
- Click on "Bot" on the left menu, then select "Reset Token"
  - Copy this token somewhere useful
- Head to the [MongoDB Atlas](https://www.mongodb.com/try?tck=community_atlas_ct) site, and add a new cluster
  - Follow the prompts to get a connection string for the application using the method of your choice
  - Copy this string somewhere useful
- Join the [HenrikDev Systems Discord](https://discord.com/invite/henrikdev-systems-704231681309278228) and get a free "Basic Key" from the `#get-a-key` channel
  - Copy this key somewhere useful, and don't give it to anyone
- Clone the repository
- In the root directory where you clone the repository, create a new file called `env.bat`
  - Add these contents (obv replacing with your values):

    ```cmd
    @echo off
    
    set "bot_token=<YOUR_BOT_TOKEN>"
    set "uri_key=<YOUR_MONGODB_STRING>"
    set "api_key=<YOUR_HENRIKDEV_KEY>"
    ```

- Install needed packages with `pip install -r requirements.txt`
- Run the command `call env.bat` in a `cmd` terminal
  - This is needed to set the proper environment variables to run the bot, and will have to be run once each session
- Run ther command `py main.py` to start the bot
- You can now make changes, and restart the bot to see what they do!

## Getting Production Data

Some development may require useful data to exist within your MongoDB cluster. While we don't currenlty support syncing data from production, you can insert this snapshot (taken 8/30/25) from production of [user](https://drive.google.com/file/d/1oYMcGSHwASfMFnrlqSdTmBlTp6id5Ltq/view?usp=sharing) and [mmr_data](https://drive.google.com/file/d/1H2z6rzdCvfCfiff5Kj6_0WyeEBTJN70-/view?usp=sharing) into your development cluster.
- There are plenty of guides online on how to do this if you have trouble.
