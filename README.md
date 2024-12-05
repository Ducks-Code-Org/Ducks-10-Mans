# Duck's 10 Mans
This is a discord bot that uses the Valorant API to create a really unique way to play Valorant with friends. It utilizes the Henrikdev Valorant API in order to function, currently serving the use of 30+ users.

Henrikdev Valorant API: https://docs.henrikdev.xyz/

![discord github](https://github.com/user-attachments/assets/0b7c3215-9f6a-4e75-b359-8440431ef7a2)

## How It's Made:
**Tech used:** Python, MongoDB, Henrikdev Valorant API

I decided to make this bot because I had been searching for one and couldn't find anything that looked compatible with what I wanted. So, why not make one myself? I designed the base functionality, and some friends decided to help out with other unique functions like formatting, updates to the stats commands, introducting classes, etc. I had never actually used an API like this one before, and I also wanted to learn how to use databases before some of the classes start teaching it so I could be ahead. It was a bit intimidating at first, trying to learn all the syntax and the discord API, but if you can get past that, it gets 10x easier. Challenges are the key to learning.

## Optimizations

Orginially, I found a bot that was able to create teams and allow you to manually report the matches, telling the discord bot exactly which team won. I want to do more than that. So, I got to work. What my bot does differently is it allows for a vote between a snake draft and balanced teams, creates a vote for the maps, and then after the match is finished, all you have to do is run the command "!report". Running the report command gets the Henrikdev API to fetch the most recent match data, match it to discord usernames, and even update their elo using a common formula. Aftewards, it saves all the data to the database. I took a common idea, and transformed it to be built directly for groups of Valorant players who want to have fun, and still play in a competitive manner.

## Lessons Learned:
I learned a LOT when I made this bot. I actually started programming this bot on my laptop... WHILE I WAS IN MY DEER STAND! I had so much free-time which also meant a great time to try to learn something new. The first few days I probably put 10+ hours into this bot, just learning all the syntax and how to access things from the database. As soon as I learned how to make a vote register with the button callbacks, I couldn't stop programming the bot. I'm convinced I was looking at my laptop rather than actually doing what I was on my stand to do. This was a massive time committment, but yet, I can't wait for the next project I take a shot at.
