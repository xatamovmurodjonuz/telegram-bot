import os
import logging
import asyncio
import asyncpg
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# --- Logger ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Environment variables ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = os.getenv("ADMIN_IDS")
DATABASE_URL = os.getenv("DATABASE_URL")

if not BOT_TOKEN or not ADMIN_IDS or not DATABASE_URL:
    logger.error("⚠️ BOT_TOKEN, ADMIN_IDS or DATABASE_URL not found!")
    exit(1)

ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS.split(",")]

# --- Bot and Dispatcher ---
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# --- Scheduler ---
scheduler = AsyncIOScheduler()

# --- PostgreSQL connection ---
pool: asyncpg.Pool = None

async def init_db():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL)

    async with pool.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS movie_files (
            id SERIAL PRIMARY KEY,
            number INTEGER UNIQUE NOT NULL,
            file_id TEXT NOT NULL
        )
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS favorites (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            movie_id INTEGER NOT NULL,
            UNIQUE(user_id, movie_id)
        )
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            movie_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            movie_id INTEGER NOT NULL,
            remind_time TIMESTAMP NOT NULL
        )
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS ratings (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            movie_id INTEGER NOT NULL,
            stars SMALLINT NOT NULL CHECK(stars BETWEEN 1 AND 5),
            UNIQUE(user_id, movie_id)
        )
        """)

# --- FSM holatlar ---
class AdminStates(StatesGroup):
    waiting_for_video = State()
    waiting_for_number = State()

class ReviewStates(StatesGroup):
    waiting_for_review = State()

class ReminderStates(StatesGroup):
    waiting_for_datetime = State()
def movie_buttons(movie_id: int, user_id: int = None) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="💖 Sevimlilarga qo'shish", callback_data=f"fav_{movie_id}"),
            InlineKeyboardButton(text="✍️ Sharh yozish", callback_data=f"review_{movie_id}")
        ],
        [
            InlineKeyboardButton(text="⏰ Eslatma o'rnatish", callback_data=f"remind_{movie_id}"),
            InlineKeyboardButton(text="📤 Do'stlarga ulashish", switch_inline_query=f"Kino #{movie_id}")
        ]
    ]

    # Add rating buttons
    rating_row = []
    for i in range(1, 6):
        rating_row.append(InlineKeyboardButton(text=f"{i}⭐", callback_data=f"rate_{movie_id}_{i}"))
    buttons.append(rating_row)

    return InlineKeyboardMarkup(inline_keyboard=buttons)


# --- /start ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT number FROM movie_files ORDER BY number")
        favs = await conn.fetch(
            "SELECT movie_id FROM favorites WHERE user_id=$1 ORDER BY movie_id",
            message.from_user.id
        )

    # Sevimlilar
    if favs:
        fav_text = "\n".join([f"Kino #{r['movie_id']}" for r in favs])
        await message.answer("💖 Sizning sevimlilaringiz:\n" + fav_text)

    if not rows:
        await message.answer("📭 Hozircha kinolar mavjud emas.")
        return

    movie_list = "\n".join([f"{r['number']}: Kino #{r['number']}" for r in rows])
    await message.answer("🎬 Kino tanlash uchun raqamini yozing:\n\n" )

# --- Kino tanlash ---
@dp.message(F.text.regexp(r"^\d+$"))
async def movie_select(message: types.Message):
    num = int(message.text)
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT file_id FROM movie_files WHERE number=$1", num)
        avg_rating = await conn.fetchval("SELECT AVG(stars) FROM ratings WHERE movie_id=$1", num)

    caption = f"Kino #{num}"
    if avg_rating:
        caption += f"\n⭐ O'rtacha reyting: {avg_rating:.1f}"

    if row:
        await message.answer_video(
            row["file_id"],
            caption=caption,
            reply_markup=movie_buttons(num)
        )
    else:
        await message.answer("❌ Bunday kino topilmadi. /start ni bosing va ro‘yxatdan tanlang.")

# --- /admin ---
@dp.message(Command("admin"))
async def cmd_admin(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Reklama, hamkorlik va homiylik masalasida murojaat uchun admin:  @FuIIstackdeveIoper1")
        return
    await message.answer("👮 Admin panelga xush kelibsiz.\n\n🎬 Iltimos, kino video faylini yuboring.")
    await state.set_state(AdminStates.waiting_for_video)

@dp.message(StateFilter(AdminStates.waiting_for_video))
async def admin_receive_video(message: types.Message, state: FSMContext):
    if not message.video:
        await message.answer("❌ Iltimos, faqat video yuboring!")
        return
    await state.update_data(video_file_id=message.video.file_id)
    await message.answer("✅ Video qabul qilindi.\nEndi unga raqam belgilang (masalan: +1, +2, +3):")
    await state.set_state(AdminStates.waiting_for_number)

@dp.message(StateFilter(AdminStates.waiting_for_number))
async def admin_receive_number(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if not (text.startswith("+") and text[1:].isdigit()):
        await message.answer("❌ Iltimos, + bilan boshlanuvchi son yuboring. Masalan: +2")
        return
    num = int(text[1:])
    data = await state.get_data()
    file_id = data.get("video_file_id")
    if not file_id:
        await message.answer("⚠️ Video topilmadi. /admin ni qaytadan boshlang.")
        await state.clear()
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO movie_files(number, file_id)
            VALUES($1, $2)
            ON CONFLICT (number) DO UPDATE SET file_id = EXCLUDED.file_id
            """,
            num, file_id
        )
    await message.answer(f"✅ Kino muvaffaqiyatli saqlandi!\n➡️ Raqami: {num}")
    await state.clear()


# --- /reviews - Admin uchun fikrlarni ko'rish ---
@dp.message(Command("reviews"))
async def cmd_reviews(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Reklama, hamkorlik va homiylik masalasida murojaat uchun admin:  @FuIIstackdeveIoper1")
        return

    async with pool.acquire() as conn:
        reviews = await conn.fetch("""
            SELECT r.*, u.first_name, u.username 
            FROM reviews r 
            LEFT JOIN messages u ON r.user_id = u.user_id 
            ORDER BY r.created_at DESC
            LIMIT 20
        """)

    if not reviews:
        await message.answer("📝 Hozircha sharhlar mavjud emas.")
        return

    response = "📝 So'nggi 20 ta sharh:\n\n"
    for review in reviews:
        user_info = f"{review['first_name'] or 'Foydalanuvchi'}"
        if review['username']:
            user_info += f" (@{review['username']})"

        response += f"👤 {user_info}\n🎬 Kino #{review['movie_id']}\n💬 {review['text']}\n⏰ {review['created_at'].strftime('%Y-%m-%d %H:%M')}\n\n"

    await message.answer(response)

# Add to favorites
@dp.callback_query(lambda c: c.data.startswith("fav_"))
async def callback_fav(callback: types.CallbackQuery):
    movie_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    async with pool.acquire() as conn:
        # Check if movie exists
        movie_exists = await conn.fetchval("SELECT 1 FROM movie_files WHERE number=$1", movie_id)
        if not movie_exists:
            await callback.answer("❌ Bu kino mavjud emas!")
            return

        # Check if already in favorites
        existing = await conn.fetchval(
            "SELECT id FROM favorites WHERE user_id=$1 AND movie_id=$2",
            user_id, movie_id
        )

        if existing:
            await conn.execute(
                "DELETE FROM favorites WHERE user_id=$1 AND movie_id=$2",
                user_id, movie_id
            )
            await callback.answer("❌ Kino sevimlilardan olib tashlandi!")
        else:
            await conn.execute(
                "INSERT INTO favorites(user_id, movie_id) VALUES($1, $2) ON CONFLICT DO NOTHING",
                user_id, movie_id
            )
            await callback.answer("💖 Kino sevimlilarga qo'shildi!")


# Write review
@dp.callback_query(lambda c: c.data.startswith("review_"))
async def callback_review(callback: types.CallbackQuery, state: FSMContext):
    movie_id = int(callback.data.split("_")[1])
    await state.update_data(movie_id=movie_id)
    await state.set_state(ReviewStates.waiting_for_review)
    await callback.message.answer("✍️ Fikringizni yozing:")
    await callback.answer()


@dp.message(StateFilter(ReviewStates.waiting_for_review))
async def process_review(message: types.Message, state: FSMContext):
    data = await state.get_data()
    movie_id = data.get("movie_id")
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO reviews(user_id, movie_id, text) VALUES($1, $2, $3)",
            message.from_user.id, movie_id, message.text
        )

        # Store user info for admin reviews
        await conn.execute("""
            INSERT INTO messages(user_id, first_name, username, text)
            VALUES($1, $2, $3, $4)
            ON CONFLICT (user_id) DO UPDATE SET
            first_name = EXCLUDED.first_name,
            username = EXCLUDED.username,
            text = EXCLUDED.text
        """, message.from_user.id, message.from_user.first_name,
                           message.from_user.username, message.text)

    await message.answer("✅ Fikringiz saqlandi va adminga yuborildi.")
    await state.clear()


# Rating
@dp.callback_query(lambda c: c.data.startswith("rate_"))
async def callback_rate(callback: types.CallbackQuery):
    _, movie_id, star = callback.data.split("_")
    movie_id = int(movie_id)
    star = int(star)
    user_id = callback.from_user.id
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO ratings(user_id, movie_id, stars)
            VALUES($1, $2, $3)
            ON CONFLICT (user_id, movie_id) DO UPDATE SET stars = EXCLUDED.stars
        """, user_id, movie_id, star)

        avg_rating = await conn.fetchval("SELECT AVG(stars) FROM ratings WHERE movie_id=$1", movie_id)

        # Update the message with new rating
        try:
            await callback.message.edit_caption(
                caption=f"Kino #{movie_id}\n⭐ O'rtacha reyting: {avg_rating:.1f}",
                reply_markup=movie_buttons(movie_id, user_id)
            )
        except:
            pass  # Message might not be editable

    await callback.answer(f"⭐ Siz {star} baho berdingiz! O'rtacha: {avg_rating:.1f}")


# Set reminder
@dp.callback_query(lambda c: c.data.startswith("remind_"))
async def callback_remind(callback: types.CallbackQuery, state: FSMContext):
    movie_id = int(callback.data.split("_")[1])
    await state.update_data(movie_id=movie_id)
    await state.set_state(ReminderStates.waiting_for_datetime)
    await callback.message.answer("⏰ Kino ko'rish vaqti va sanasini yozing (YYYY-MM-DD HH:MM):")
    await callback.answer()


@dp.message(StateFilter(ReminderStates.waiting_for_datetime))
async def process_reminder(message: types.Message, state: FSMContext):
    try:
        dt = datetime.strptime(message.text.strip(), "%Y-%m-%d %H:%M")
        if dt <= datetime.now():
            await message.answer("❌ Kechikkan vaqt! Iltimos, kelajakdagi vaqtni kiriting.")
            return
    except ValueError:
        await message.answer("❌ Noto'g'ri format. Iltimos YYYY-MM-DD HH:MM formatda yuboring.")
        return

    data = await state.get_data()
    movie_id = data.get("movie_id")
    user_id = message.from_user.id

    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO reminders(user_id, movie_id, remind_time) VALUES($1,$2,$3)",
            user_id, movie_id, dt
        )

    # Add to scheduler
    scheduler.add_job(
        send_reminder,
        'date',
        run_date=dt,
        args=[user_id, movie_id]
    )

    await message.answer(f"✅ Eslatma o'rnatildi! {dt.strftime('%Y-%m-%d %H:%M')} da eslatib beraman.")
    await state.clear()


async def send_reminder(user_id: int, movie_id: int):
    async with pool.acquire() as conn:
        file_id = await conn.fetchval("SELECT file_id FROM movie_files WHERE number=$1", movie_id)

    if file_id:
        await bot.send_message(user_id, f"⏰ Esingizda! Kino #{movie_id} vaqti keldi!")
        await bot.send_video(user_id, file_id, caption=f"Kino #{movie_id}")


# --- /myfavorites - View favorites ---
@dp.message(Command("myfavorites"))
async def cmd_myfavorites(message: types.Message):
    try:
        async with pool.acquire() as conn:
            favs = await conn.fetch(
                """SELECT m.number 
                FROM favorites f 
                JOIN movie_files m ON f.movie_id = m.number 
                WHERE f.user_id = $1 
                ORDER BY m.number""",
                message.from_user.id
            )

        if not favs:
            await message.answer("💔 Sizda hali sevimli kinolar yo'q.")
            return

        fav_text = "\n".join([f"{i + 1}. Kino #{r['number']}" for i, r in enumerate(favs)])
        await message.answer(f"💖 Sizning sevimli kinolaringiz:\n\n{fav_text}\n\nKo'rish uchun kino raqamini yozing.")
    except Exception as e:
        logger.error(f"cmd_myfavorites xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi. Iltimos, keyinroq urunib ko'ring.")


# --- /mystats - Statistics ---
@dp.message(Command("mystats"))
async def cmd_mystats(message: types.Message):
    try:
        user_id = message.from_user.id

        async with pool.acquire() as conn:
            # Favorites count
            fav_count = await conn.fetchval(
                "SELECT COUNT(*) FROM favorites WHERE user_id = $1",
                user_id
            )

            # Reviews count
            review_count = await conn.fetchval(
                "SELECT COUNT(*) FROM reviews WHERE user_id = $1",
                user_id
            )

            # Ratings count
            rating_count = await conn.fetchval(
                "SELECT COUNT(*) FROM ratings WHERE user_id = $1",
                user_id
            )

            # Reminders count
            reminder_count = await conn.fetchval(
                "SELECT COUNT(*) FROM reminders WHERE user_id = $1",
                user_id
            )

        stats_text = (
            f"📊 Sizning statistikangiz:\n\n"
            f"💖 Sevimlilar: {fav_count}\n"
            f"✍️ Sharhlar: {review_count}\n"
            f"⭐ Reytinglar: {rating_count}\n"
            f"⏰ Eslatmalar: {reminder_count}"
        )

        await message.answer(stats_text)
    except Exception as e:
        logger.error(f"cmd_mystats xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi. Iltimos, keyinroq urunib ko'ring.")


# --- Default response ---
@dp.message()
async def unknown_message(message: types.Message):
    await message.answer("🤖 Noma'lum buyruq.\n/start ni bosing yoki kinoning raqamini yozing.")


# --- Run ---
async def main():
    await init_db()
    scheduler.start()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
