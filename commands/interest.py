"Plans a time to run Duck's 10 Mans and open a Join/Leave interest view."

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pymongo import ReturnDocument

import discord
from discord.ext import commands

from commands import BotCommands
from database import interests
from views.interest_view import InterestView


async def setup(bot):
    await bot.add_cog(InterestCommand(bot))


class InterestCommand(BotCommands):
    @commands.command(name="interest")
    async def interest(self, ctx, *, time: str = None):
        """
        Usage:
          !interest 9pm
          !interest tomorrow 7
          !interest 8/22 9:30pm
          !interest in 2h
          !interest list
        """
        if time is None:
            await ctx.send(
                "Usage: `!interest <time>` (e.g., `!interest 9pm`) or `!interest list`."
            )
            return

        if time.strip().lower() in {"list", "ls"}:
            now_utc = datetime.now(timezone.utc)
            upcoming = list(
                interests.find({"scheduled_at_utc": {"$gte": now_utc}})
                .sort("scheduled_at_utc", 1)
                .limit(8)
            )
            if not upcoming:
                await ctx.send(
                    "No upcoming interest slots yet. Create one with `!interest 9pm`."
                )
                return

            tz = ZoneInfo("America/Chicago")
            lines = []
            for doc in upcoming:
                t_utc = doc.get("scheduled_at_utc")
                stamp = int(t_utc.timestamp())
                count = len(doc.get("interested_ids", []))
                t_local = t_utc.astimezone(tz)
                lines.append(
                    f"• **{t_local.strftime('%Y-%m-%d %I:%M %p %Z')}** — <t:{stamp}:R>  (**{count}** interested)"
                )
            await ctx.send("**Upcoming 10 Mans interest slots:**\n" + "\n".join(lines))
            return

        dt_utc, err = parse_time_to_utc(time)
        if err:
            await ctx.send(err)
            return

        # Round to 5 minutes
        rounded = dt_utc.replace(second=0, microsecond=0)
        minute = (rounded.minute // 5) * 5
        rounded = rounded.replace(minute=minute)

        # Ensure the doc exists
        doc = interests.find_one_and_update(
            {"scheduled_at_utc": rounded},
            {
                "$setOnInsert": {
                    "scheduled_at_utc": rounded,
                    "created_by": str(ctx.author.id),
                    "created_at_utc": datetime.now(timezone.utc),
                },
                "$addToSet": {"interested_ids": str(ctx.author.id)},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )

        view = InterestView(rounded, timeout=None)
        tz = ZoneInfo("America/Chicago")
        embed = discord.Embed(
            description="Creating interest slot…", color=discord.Color.green()
        )
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg

        await view._render(await ctx.fetch_message(msg.id))


def parse_time_to_utc(time: str):
    if not time:
        return (
            None,
            "Provide a time, e.g. `!interest 9pm` or `!interest tomorrow 7`.",
        )

    tz = ZoneInfo("America/Chicago")
    now_local = datetime.now(tz)

    s = time.strip().lower()
    # Relative: "in 2h", "in 45m"
    if s.startswith("in "):
        parts = s[3:].strip()
        mins = 0
        try:
            if parts.endswith("h"):
                hrs = float(parts[:-1].strip())
                mins = int(hrs * 60)
            elif parts.endswith("m"):
                mins = int(parts[:-1].strip())
            else:
                hrs, mins_part = 0, 0
                if "h" in parts:
                    h_chunk, rest = parts.split("h", 1)
                    hrs = int(h_chunk.strip() or 0)
                    parts = rest.strip()
                if parts.endswith("m"):
                    mins_part = int(parts[:-1].strip() or 0)
                mins = hrs * 60 + mins_part
            target_local = now_local + timedelta(minutes=mins)
            return target_local.astimezone(timezone.utc), None
        except Exception:
            return None, "Couldn’t parse relative time. Try `in 2h` or `in 45m`."

    # Normalize helper functions
    def try_formats(candidate, fmts):
        for f in fmts:
            try:
                return datetime.strptime(candidate, f)
            except Exception:
                pass
        return None

    tokens = s.split()
    base_date = now_local.date()

    # Handle "today" / "tomorrow"
    if tokens and tokens[0] in {"today", "tomorrow"}:
        if tokens[0] == "tomorrow":
            base_date = base_date + timedelta(days=1)
        time_str = " ".join(tokens[1:]).strip()
        if not time_str:
            return None, "Add a time after today/tomorrow, e.g. `tomorrow 9pm`."

        t_try = try_formats(time_str, ["%I%p", "%I:%M%p", "%H:%M", "%H"])
        if not t_try:
            return (
                None,
                "Couldn’t parse time. Try formats like `9pm`, `9:30pm`, `21:00`.",
            )
        dt_local = datetime(
            base_date.year,
            base_date.month,
            base_date.day,
            t_try.hour,
            t_try.minute,
            tzinfo=tz,
        )
        return dt_local.astimezone(timezone.utc), None

    only_time = try_formats(s, ["%I%p", "%I:%M%p", "%H:%M", "%H"])
    if only_time:
        dt_local = datetime(
            base_date.year,
            base_date.month,
            base_date.day,
            only_time.hour,
            only_time.minute,
            tzinfo=tz,
        )
        if dt_local < now_local:
            dt_local = dt_local + timedelta(days=1)
        return dt_local.astimezone(timezone.utc), None

    default_hour, default_minute = 21, 0

    dt = try_formats(
        s, ["%Y-%m-%d %H:%M", "%Y-%m-%d %I:%M%p", "%Y-%m-%d %I%p", "%Y-%m-%d"]
    )
    if dt:
        if dt.hour == 0 and len(s.strip().split()) == 1:
            dt = dt.replace(hour=default_hour, minute=default_minute)
        return dt.replace(tzinfo=tz).astimezone(timezone.utc), None

    for date_sep in ["/", "-"]:
        try:
            if " " in s:
                date_part, time_part = s.split(" ", 1)
            else:
                date_part, time_part = s, ""

            if date_sep in date_part:
                m, d = [int(x) for x in date_part.split(date_sep)]
                y = now_local.year

                base = datetime(y, m, d, tzinfo=tz)
                if not time_part:
                    dt_local = base.replace(hour=default_hour, minute=default_minute)
                else:
                    t_try = try_formats(
                        time_part.strip(), ["%I%p", "%I:%M%p", "%H:%M", "%H"]
                    )
                    if not t_try:
                        return (
                            None,
                            "Couldn’t parse the time. Try `8/22 9pm` or `8-22 21:00`.",
                        )
                    dt_local = datetime(y, m, d, t_try.hour, t_try.minute, tzinfo=tz)
                return dt_local.astimezone(timezone.utc), None
        except Exception:
            pass
    return (
        None,
        "Couldn’t understand that time. Examples: `9pm`, `tomorrow 7`, `8/22 9:30pm`, `in 2h`.",
    )
