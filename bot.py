# bot.py

import os
import re
from datetime import datetime, date, timedelta, time
from typing import List, Tuple

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters, CommandHandler

from schedule_data import WEEK1, WEEK2, Lesson

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID", "0"))
TZ_NAME = os.getenv("TZ", "Asia/Novosibirsk").strip()

import pytz
TZ = pytz.timezone(TZ_NAME)


# 2 февраля 2026 — старт 1-й недели
WEEK_START = date(2026, 2, 2)

DAYS_RU = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]

# Чтобы не спамить — запоминаем, за какую дату уже отправляли “последняя пара началась”
_lastpair_notified_for: date | None = None


def week_type(d: date) -> int:
    delta_days = (d - WEEK_START).days
    week_index = delta_days // 7
    return 1 if (week_index % 2 == 0) else 2


def get_lessons_for_date(d: date) -> List[Lesson]:
    wt = week_type(d)
    weekday = d.weekday()
    return (WEEK1 if wt == 1 else WEEK2).get(weekday, [])


def analyze_day(now: datetime, lessons: List[Lesson]) -> Tuple[int, str]:
    if not lessons:
        return 0, "сегодня пар нет"

    t = now.timetz().replace(tzinfo=None)

    if t < lessons[0].start:
        return len(lessons), f"пары ещё не начались (первая в {lessons[0].start.strftime('%H:%M')})"

    for i, les in enumerate(lessons, start=1):
        if les.start <= t <= les.end:
            remaining = len(lessons) - i + 1
            return remaining, f"идёт {i}-я пара ({les.start.strftime('%H:%M')}–{les.end.strftime('%H:%M')}): {les.title}"

        if i < len(lessons):
            nxt = lessons[i]
            if les.end < t < nxt.start:
                remaining = len(lessons) - i
                return remaining, f"сейчас перерыв/окно, следующая {i+1}-я в {nxt.start.strftime('%H:%M')}"

    return 0, f"пары закончились (последняя до {lessons[-1].end.strftime('%H:%M')})"


def format_answer(now: datetime, target_date: date) -> str:
    wt = week_type(target_date)
    lessons = get_lessons_for_date(target_date)
    day_name = DAYS_RU[target_date.weekday()]

    if target_date == now.date():
        remaining, status = analyze_day(now, lessons)
        total = len(lessons)
        return (
            f"📅 {day_name} — {wt}-я неделя\n"
            f"📚 Всего пар сегодня: {total}\n"
            f"▶️ Сейчас: {status}\n"
            f"⏳ Осталось пар: {remaining}"
        )

    if not lessons:
        return f"📅 {day_name} — {wt}-я неделя\n🏖️ Пар нет"

    lines = [f"📅 {day_name} — {wt}-я неделя", f"📚 Всего пар: {len(lessons)}", ""]
    for idx, les in enumerate(lessons, start=1):
        lines.append(f"{idx}) {les.start.strftime('%H:%M')}–{les.end.strftime('%H:%M')} — {les.title}")
    return "\n".join(lines)


def is_allowed(update: Update) -> bool:
    user = update.effective_user
    return bool(user and user.id == ALLOWED_USER_ID)


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def intent(text: str) -> str:
    t = normalize_text(text)
    if any(x in t for x in ["завтра", "на завтра"]):
        return "tomorrow"
    if any(x in t for x in ["сегодня", "на сегодня", "сколько сегодня", "пары сегодня", "расписание сегодня"]):
        return "today"
    if any(x in t for x in ["сколько пар", "сколько сегодня пар", "сколько пар сегодня"]):
        return "today"
    if any(x in t for x in ["какая сейчас", "сейчас какая", "какая пара", "что сейчас", "сейчас пара"]):
        return "today"
    return "help"


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return
    await update.message.reply_text(
        "Привет! Пиши:\n"
        "• 'сколько сегодня пар'\n"
        "• 'какая сейчас пара'\n"
        "• 'завтра пары'\n"
        "Я считаю по времени Новосибирска.\n"
        "Также я пришлю сообщение, когда начнётся последняя пара."
    )


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return
    now = datetime.now(TZ)
    await update.message.reply_text(format_answer(now, now.date()))


async def cmd_tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return
    now = datetime.now(TZ)
    target = now.date() + timedelta(days=1)
    await update.message.reply_text(format_answer(now, target))


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return

    now = datetime.now(TZ)
    it = intent(update.message.text)

    if it == "today":
        await update.message.reply_text(format_answer(now, now.date()))
    elif it == "tomorrow":
        await update.message.reply_text(format_answer(now, now.date() + timedelta(days=1)))
    else:
        await update.message.reply_text(
            "Попробуй: 'сколько сегодня пар', 'какая сейчас пара', 'завтра пары'."
        )


async def last_pair_watcher(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Раз в минуту проверяем: началась ли последняя пара сегодня."""
    global _lastpair_notified_for

    now = datetime.now(TZ)
    today = now.date()

    lessons = get_lessons_for_date(today)
    if not lessons:
        return

    last = lessons[-1]
    # Отправляем только один раз в день
    if _lastpair_notified_for == today:
        return

    # Считаем “момент старта последней пары” с точностью до минуты
    if now.hour == last.start.hour and now.minute == last.start.minute:
        try:
            await context.bot.send_message(
                chat_id=ALLOWED_USER_ID,
                text=f"🔔 Началась последняя пара ({last.start.strftime('%H:%M')}–{last.end.strftime('%H:%M')}): {last.title}",
            )
            _lastpair_notified_for = today
        except Exception:
            # если вдруг сеть/телега глюкнет — не падаем
            return


def main() -> None:
    if not TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN пуст. Заполни переменные окружения.")
    if ALLOWED_USER_ID == 0:
        raise SystemExit("ALLOWED_USER_ID пуст. Заполни переменные окружения.")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("tomorrow", cmd_tomorrow))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    # Каждую минуту проверяем старт последней пары
    app.job_queue.run_repeating(last_pair_watcher, interval=60, first=5)

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

