# Duck's 10 Mans

Duck's 10 Mans is a highly customized discord bot that manages private VALORANT matches, with its own queue system, MMR engine, and leaderboard. To get match results, the bot uses the [Henrikdev Valorant API Wrapper](https://docs.henrikdev.xyz), and the project currently serves more than 80 users. 

This project is Open Source, and can easily be forked and changed to fit users needs. If you would like to contribute to this project, please see [CONTRIBUTING.md](https://github.com/Ducks-Code-Org/Ducks-10-Mans/blob/main/CONTRIBUTING.md) and [GETTING_STARTED.md](https://github.com/Ducks-Code-Org/Ducks-10-Mans/blob/main/GETTING_STARTED.md).

## How It Works

First, users start a new queue with `!signup`. A new match channel is generated that allows users to freely join and leave the queue until the queue reaches 10 players.

### Example Signup Queue

<img width="425" height="249" alt="image" src="https://github.com/user-attachments/assets/40e8e1c8-0ce5-4c2a-9ea6-a3ca8db19b70" />

Next, the users go through the match setup process voting on the following:
- Teams - Balanced based on MMR or Chosen by Captains Draft
- Map Pool - Competetive Map Pool or All Standard Maps
- Map - Players choose from 3 randomly chosen maps from the pool

Then, the users simply play VALORANT match, and run the `!report` command once they are finished. This command will update all user statistics and update players' MMR based on the match results.

Finally, a leaderboard with all players' statistics may be viewed with the `!leaderboard` command.

### Example Leaderboard

![Screenshot 2024-12-10 175156](https://github.com/user-attachments/assets/a295117e-1f43-4002-ab3d-dc5740dc08e1)

## How It's Made

**Tech used:** Python, MongoDB, Henrikdev Valorant API

> I decided to make this bot because I had been searching for one and couldn't find anything that looked compatible with what I wanted. So, why not make one myself? I designed the base functionality, and some friends decided to help out with other unique functions like formatting, updates to the stats commands, introducting classes, etc. I had never actually used an API like this one before, and I also wanted to learn how to use databases before some of the classes start teaching it so I could be ahead. It was a bit intimidating at first, trying to learn all the syntax and the discord API, but if you can get past that, it gets 10x easier. Challenges are the key to learning.
>
> — *Colton (Duck)*

## Optimizations

> Orginially, I found a bot that was able to create teams and allow you to manually report the matches, telling the discord bot exactly which team won. I want to do more than that. So, I got to work. What my bot does differently is it allows for a vote between a snake draft and balanced teams, creates a vote for the maps, and then after the match is finished, all you have to do is run the command "!report". Running the report command gets the Henrikdev API to fetch the most recent match data, match it to discord usernames, and even update their elo using a common formula. Aftewards, it saves all the data to the database. I took a common idea, and transformed it to be built directly for groups of Valorant players who want to have fun, and still play in a competitive manner.
>
> — *Colton (Duck)*

## Lessons Learned

> I learned a LOT when I made this bot. I actually started programming this bot on my laptop... WHILE I WAS IN MY DEER STAND! I had so much free-time which also meant a great time to try to learn something new. The first few days I probably put 10+ hours into this bot, just learning all the syntax and how to access things from the database. As soon as I learned how to make a vote register with the button callbacks, I couldn't stop programming the bot. I'm convinced I was looking at my laptop rather than actually doing what I was on my stand to do. This was a massive time committment, but yet, I can't wait for the next project I take a shot at.
>
> — *Colton (Duck)*
