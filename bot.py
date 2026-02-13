import os
import re
from datetime import datetime, date, timedelta
from typing import List, Tuple, Optional

from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    MessageHandler,
    ContextTypes,
    filters,
    CommandHandler,
)

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

# Чтобы не спамить уведомлениями (по одному разу в день на каждый тип)
_oneleft_notified_for: date | None = None
_lastpair_notified_for: date | None = None

# Кнопки
BTN_TODAY = "📅 Сегодня"
BTN_TOMORROW = "📅 Завтра"
BTN_DATE = "📝 Написать дату"
BTN_WEEK = "🗓️ Неделя"

MAIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(BTN_TODAY), KeyboardButton(BTN_TOMORROW)],
        [KeyboardButton(BTN_DATE), KeyboardButton(BTN_WEEK)],
    ],
    resize_keyboard=True,
    one_time_keyboard=False,
)

MONTHS_RU = {
    "января": 1, "январь": 1,
    "февраля": 2, "февраль": 2,
    "марта": 3, "март": 3,
    "апреля": 4, "апрель": 4,
    "мая": 5, "май": 5,
    "июня": 6, "июнь": 6,
    "июля": 7, "июль": 7,
    "августа": 8, "август": 8,
    "сентября": 9, "сентябрь": 9,
    "октября": 10, "октябрь": 10,
    "ноября": 11, "ноябрь": 11,
    "декабря": 12, "декабрь": 12,
}


def is_allowed(update: Update) -> bool:
    user = update.effective_user
    return bool(user and user.id == ALLOWED_USER_ID)


def week_type(d: date) -> int:
    delta_days = (d - WEEK_START).days
    week_index = delta_days // 7
    return 1 if (week_index % 2 == 0) else 2


def get_lessons_for_date(d: date) -> List[Lesson]:
    wt = week_type(d)
    weekday = d.weekday()
    return (WEEK1 if wt == 1 else WEEK2).get(weekday, [])


def analyze_day(now: datetime, lessons: List[Lesson]) -> Tuple[int, str]:
    """Возвращает (сколько пар осталось, статус сейчас)."""
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


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def parse_date_from_text(text: str, now: datetime) -> Optional[date]:
    """
    Понимает:
    - 23.02
    - 23.02.2026
    - 23/02 или 23-02
    - 23 февраля
    - 23 февраля 2026
    """
    t = normalize_text(text)

    # dd.mm(.yyyy) / dd-mm / dd/mm
    m = re.search(r"\b(\d{1,2})[.\-\/](\d{1,2})(?:[.\-\/](\d{2,4}))?\b", t)
    if m:
        dd = int(m.group(1))
        mm = int(m.group(2))
        yy = m.group(3)
        if yy:
            y = int(yy)
            if y < 100:
                y += 2000
        else:
            y = now.year
        try:
            return date(y, mm, dd)
        except ValueError:
            return None

    # dd <monthname> [yyyy]
    m2 = re.search(r"\b(\d{1,2})\s+([а-яё]+)(?:\s+(\d{4}))?\b", t)
    if m2:
        dd = int(m2.group(1))
        mon = m2.group(2)
        y = int(m2.group(3)) if m2.group(3) else now.year
        mm = MONTHS_RU.get(mon)
        if not mm:
            return None
        try:
            return date(y, mm, dd)
        except ValueError:
            return None

    return None


def format_week(now: datetime, start: date, days: int = 7) -> str:
    lines = ["🗓️ Расписание на 7 дней:"]
    for i in range(days):
        d = start + timedelta(days=i)
        day_name = DAYS_RU[d.weekday()]
        wt = week_type(d)
        lessons = get_lessons_for_date(d)

        if not lessons:
            lines.append(f"\n📅 {day_name} ({d.strftime('%d.%m')}) — {wt}-я неделя\n🏖️ Пар нет")
            continue

        lines.append(f"\n📅 {day_name} ({d.strftime('%d.%m')}) — {wt}-я неделя\n📚 Пар: {len(lessons)}")
        for idx, les in enumerate(lessons, start=1):
            lines.append(f"{idx}) {les.start.strftime('%H:%M')}–{les.end.strftime('%H:%M')} — {les.title}")

    return "\n".join(lines)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return
    context.user_data["awaiting_date"] = False
    await update.message.reply_text(
        "Привет! 👋\n"
        "Нажимай кнопки:\n"
        f"• {BTN_TODAY}\n"
        f"• {BTN_TOMORROW}\n"
        f"• {BTN_DATE} (например: 23 февраля или 23.02)\n"
        f"• {BTN_WEEK} (расписание на 7 дней)\n\n"
        "Время: " + TZ_NAME,
        reply_markup=MAIN_KB,
    )


async def show_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    now = datetime.now(TZ)
    await update.message.reply_text(format_answer(now, now.date()), reply_markup=MAIN_KB)


async def show_tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    now = datetime.now(TZ)
    target = now.date() + timedelta(days=1)
    await update.message.reply_text(format_answer(now, target), reply_markup=MAIN_KB)


async def ask_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["awaiting_date"] = True
    await update.message.reply_text(
        "Напиши дату, например:\n"
        "• 23 февраля\n"
        "• 23.02\n"
        "• 23.02.2026",
        reply_markup=MAIN_KB,
    )


async def show_week(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    now = datetime.now(TZ)
    await update.message.reply_text(format_week(now, now.date(), days=7), reply_markup=MAIN_KB)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return

    txt = (update.message.text or "").strip()
    now = datetime.now(TZ)

    # Кнопки
    if txt == BTN_TODAY:
        context.user_data["awaiting_date"] = False
        return await show_today(update, context)

    if txt == BTN_TOMORROW:
        context.user_data["awaiting_date"] = False
        return await show_tomorrow(update, context)

    if txt == BTN_DATE:
        return await ask_date(update, context)

    if txt == BTN_WEEK:
        context.user_data["awaiting_date"] = False
        return await show_week(update, context)

    # Дата текстом
    d = parse_date_from_text(txt, now)
    if d:
        context.user_data["awaiting_date"] = False
        await update.message.reply_text(format_answer(now, d), reply_markup=MAIN_KB)
        return

    # Если ждали дату — но ввели не дату
    if context.user_data.get("awaiting_date"):
        await update.message.reply_text(
            "Не понял дату 😅\nПопробуй: 23 февраля / 23.02 / 23.02.2026",
            reply_markup=MAIN_KB,
        )
        return

    # Фразы на всякий случай
    t = normalize_text(txt)
    if "сегодня" in t:
        return await show_today(update, context)
    if "завтра" in t:
        return await show_tomorrow(update, context)

    await update.message.reply_text(
        f"Нажми {BTN_TODAY}, {BTN_TOMORROW}, {BTN_DATE} или {BTN_WEEK}.",
        reply_markup=MAIN_KB,
    )


async def notify(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    try:
        await context.bot.send_message(chat_id=ALLOWED_USER_ID, text=text)
    except Exception:
        return


async def notifier(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Уведомления:
    - 'осталась 1 пара' (когда начинается предпоследняя, или единственная если всего 1)
    - 'началась последняя пара' (НО без дубля: если всего 1 пара — шлём только одно сообщение)
    """
    global _oneleft_notified_for, _lastpair_notified_for

    now = datetime.now(TZ)
    today = now.date()
    lessons = get_lessons_for_date(today)
    if not lessons:
        return

    n = len(lessons)

    # Момент, когда "останется 1 пара"
    target_idx = (n - 2) if n >= 2 else 0
    target = lessons[target_idx]

    # Момент старта последней пары
    last = lessons[-1]

    # 1) Осталась 1 пара
    if _oneleft_notified_for != today:
        if now.hour == target.start.hour and now.minute == target.start.minute:
            if n == 1:
                await notify(
                    context,
                    f"🔔 Сегодня всего 1 пара. Она началась ({target.start.strftime('%H:%M')}–{target.end.strftime('%H:%M')}): {target.title}"
                )
            else:
                await notify(
                    context,
                    f"🔔 Осталась 1 пара до конца дня. Сейчас началась {target_idx+1}-я ({target.start.strftime('%H:%M')}–{target.end.strftime('%H:%M')}): {target.title}"
                )
            _oneleft_notified_for = today

    # 2) Началась последняя пара — без дубля (если всего 1 пара, то уже отправили выше)
    if _lastpair_notified_for != today:
        if now.hour == last.start.hour and now.minute == last.start.minute:
            if n == 1:
                _lastpair_notified_for = today
                return
            await notify(
                context,
                f"🔔 Началась последняя пара ({last.start.strftime('%H:%M')}–{last.end.strftime('%H:%M')}): {last.title}"
            )
            _lastpair_notified_for = today


def main() -> None:
    if not TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN пуст. Заполни переменные окружения.")
    if ALLOWED_USER_ID == 0:
        raise SystemExit("ALLOWED_USER_ID пуст. Заполни переменные окружения.")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    # Каждую минуту проверяем уведомления
    app.job_queue.run_repeating(notifier, interval=60, first=5)

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
