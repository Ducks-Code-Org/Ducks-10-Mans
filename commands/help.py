import discord
from discord.ext import commands


async def setup(bot):
    await bot.add_cog(HelpCommand(bot))


def _chunk_embed_lines(lines: list[str], limit: int = 1000) -> list[str]:
    """Split command-list lines into embed-field-sized chunks.

    Discord rejects any embed field whose value exceeds 1024 characters
    (error 50035), so a long command list must be spread across fields.
    Each returned chunk is a newline join of whole lines. Mirrors the helper
    in commands/maintenance_commands.py used by !adminhelp.
    """
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for line in lines:
        extra = len(line) + (1 if current else 0)  # +1 for the joining newline
        if current and length + extra > limit:
            chunks.append("\n".join(current))
            current = []
            length = 0
            extra = len(line)
        current.append(line)
        length += extra
    if current:
        chunks.append("\n".join(current))
    return chunks or ["—"]


# (usage arguments, short description) per command, grouped by the section
# they render under — mirrors ADMIN_COMMAND_HELP in
# commands/maintenance_commands.py so !help and !adminhelp share one style.
HELP_SECTIONS: list[tuple[str, list[tuple[str, str, str]]]] = [
    (
        "10 Mans",
        [
            ("signup", "", "Start a new 10 mans signup session"),
            ("report", "", "Report match results and update MMR"),
            (
                "interest",
                "<time>",
                "Plan a time to play 10 mans (`!interest list` for upcoming)",
            ),
            ("pingrecent", "", "Ping everyone from the most recent queue"),
        ],
    ),
    (
        "Stats",
        [
            ("stats", "<Name#Tag|@user>", "Check a player's MMR and match statistics"),
            ("linkriot", "<Name#Tag>", "Link your Riot account"),
            ("ranks", "", "View rank roles and their MMR thresholds"),
            (
                "leaderboard",
                "<type>",
                "View the leaderboard\n↪ _Types: `mmr` (default), `rating`, `wins`, `losses`, `kd`, `acs`, `coins`_",
            ),
        ],
    ),
    (
        "Duck Coins",
        [
            ("coins", "<Name#Tag|@user>", "Check Duck Coin balance (yours by default)"),
            ("bet", "attackers|defenders <amount>", "Bet Duck Coins on the match"),
            ("doubledown", "", "Spend 5 Duck Coins to double your MMR change"),
            (
                "setmap",
                "<map> <amount>",
                "Wager Duck Coins (min 3) to override the chosen map",
            ),
        ],
    ),
    (
        "Utility",
        [
            ("bug", "", "Report a bug (pings the maintainers)"),
        ],
    ),
]


class HelpCommand(commands.Cog):
    @commands.command()
    async def help(self, ctx):
        help_embed = discord.Embed(
            title="Help Menu",
            description="Duck's 10 Mans Bot Commands:",
            color=discord.Color.green(),
        )

        # Same layout as !adminhelp: one non-inline field per section, each
        # command rendered as `!usage` — description, chunked to stay under
        # Discord's 1024-character embed field limit.
        for section, entries in HELP_SECTIONS:
            lines = []
            for name, usage_args, desc in entries:
                usage = f"!{name} {usage_args}".strip()
                lines.append(f"`{usage}` — {desc}")
            chunks = _chunk_embed_lines(lines)
            for i, chunk in enumerate(chunks, start=1):
                field = (
                    section if len(chunks) == 1 else f"{section} ({i}/{len(chunks)})"
                )
                help_embed.add_field(name=field, value=chunk, inline=False)

        help_embed.set_footer(text="Admins: !adminhelp lists maintenance commands.")

        await ctx.send(embed=help_embed)
