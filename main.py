"""
PX Bot v1.2.0 - Flat Version (No Folders)
Professional Telegram Config Seller + Web Admin Panel
Everything in one file - ready for GitHub & Railway
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
import shutil
import sys
import time
import uuid
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

import httpx
import psutil
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
try:
    from aiogram.enums import ButtonStyle
except ImportError:
    # Fallback for older aiogram
    class ButtonStyle:
        SUCCESS = "success"
        DANGER = "danger"
        PRIMARY = "primary"
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import (
    String, Integer, BigInteger, Boolean, Text, Float,
    DateTime, ForeignKey, JSON, func, select, desc,
)
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from starlette.middleware.sessions import SessionMiddleware
import uvicorn

# ============================================================
# PATHS & LOGGING
# ============================================================
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT
BACKUP_DIR = ROOT / "backups"
BACKUP_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("pxbot")

# ============================================================
# CONFIG
# ============================================================
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    BOT_TOKEN: str = ""
    ADMIN_IDS: str = ""
    DATABASE_URL: str = f"sqlite+aiosqlite:///{ROOT / 'pxbot.db'}"
    SECRET_KEY: str = "px-bot-super-secret-change-me-please-32chars"
    WEB_HOST: str = "0.0.0.0"
    WEB_PORT: int = 8000
    VERSION: str = "1.2.0"

    @property
    def admin_ids_list(self) -> List[int]:
        if not self.ADMIN_IDS:
            return []
        raw = self.ADMIN_IDS.replace(",", "\n")
        return [int(x.strip()) for x in raw.splitlines() if x.strip().isdigit()]

    @property
    def is_configured(self) -> bool:
        return bool(self.BOT_TOKEN and self.admin_ids_list)

settings = Settings()

def reload_settings():
    """Reload settings from .env (after /setup)"""
    global settings
    settings = Settings()
    return settings
START_TIME = time.time()

# ============================================================
# DATABASE MODELS
# ============================================================
class Base(DeclarativeBase):
    pass

class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    balance: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    orders: Mapped[list["Order"]] = relationship(back_populates="user")

class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price: Mapped[float] = mapped_column(Float)
    duration_days: Mapped[int] = mapped_column(Integer, default=30)
    data_limit_gb: Mapped[float] = mapped_column(Float, default=0)
    panel_id: Mapped[Optional[int]] = mapped_column(ForeignKey("panels.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    panel: Mapped[Optional["Panel"]] = relationship()
    orders: Mapped[list["Order"]] = relationship(back_populates="product")

class Panel(Base):
    __tablename__ = "panels"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64))
    panel_type: Mapped[str] = mapped_column(String(32))
    base_url: Mapped[str] = mapped_column(String(256))
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    password: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_test_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    last_test: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    amount: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    receipt_file_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    config_data: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    panel_username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    user: Mapped["User"] = relationship(back_populates="orders")
    product: Mapped["Product"] = relationship(back_populates="orders")

class PaymentMethod(Base):
    __tablename__ = "payment_methods"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64))
    card_number: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    card_owner: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

class ForceJoin(Base):
    __tablename__ = "force_joins"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[str] = mapped_column(String(64))
    channel_title: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

class Review(Base):
    __tablename__ = "reviews"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    rating: Mapped[int] = mapped_column(Integer)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

# ============================================================
# DATABASE ENGINE
# ============================================================
engine = create_async_engine(settings.DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    defaults = {
        "rules": (
            "📜 قوانین استفاده از PX Bot\n\n"
            "۱. استفاده از سرویس فقط برای اهداف قانونی مجاز است.\n"
            "۲. فروش مجدد یا اشتراک‌گذاری کانفیگ ممنوع است.\n"
            "۳. در صورت تخلف، سرویس بدون بازگشت وجه قطع می‌شود.\n"
            "۴. پشتیبانی فقط از طریق تیکت داخل ربات انجام می‌شود.\n"
            "۵. با زدن دکمه «پذیرش قوانین» موافقت خود را اعلام می‌کنید."
        ),
        "welcome_text": "🎉 به PX Bot خوش آمدید!\n\nسرویس فروش کانفیگ حرفه‌ای و سریع.",
        "support_text": "💬 پشتیبانی PX Bot\n\nپیام خود را بنویسید:",
        "card_number": "",
        "card_owner": "",
    }
    async with AsyncSessionLocal() as session:
        for key, value in defaults.items():
            if not await session.get(Setting, key):
                session.add(Setting(key=key, value=value))
        await session.commit()

async def get_setting(key: str, default: str = "") -> str:
    async with AsyncSessionLocal() as session:
        row = await session.get(Setting, key)
        return row.value if row and row.value is not None else default

async def set_setting(key: str, value: str):
    async with AsyncSessionLocal() as session:
        row = await session.get(Setting, key)
        if row:
            row.value = value
        else:
            session.add(Setting(key=key, value=value))
        await session.commit()

# ============================================================
# HELPERS
# ============================================================
def get_uptime() -> str:
    seconds = int(time.time() - START_TIME)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days: parts.append(f"{days} روز")
    if hours: parts.append(f"{hours} ساعت")
    if minutes: parts.append(f"{minutes} دقیقه")
    parts.append(f"{secs} ثانیه")
    return " و ".join(parts)

def format_price(price: float) -> str:
    return f"{int(price):,}".replace(",", "٬") + " تومان"

def generate_username(telegram_id: int, order_id: int) -> str:
    return f"px_{telegram_id}_{order_id}"

def is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids_list

def create_backup() -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = BACKUP_DIR / f"pxbot_backup_{ts}.zip"
    db_file = ROOT / "pxbot.db"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        if db_file.exists():
            zf.write(db_file, "pxbot.db")
    return path

def restore_backup(zip_path: Path) -> bool:
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            if "pxbot.db" in zf.namelist():
                target = ROOT / "pxbot.db"
                if target.exists():
                    shutil.copy(target, ROOT / f"pxbot_before_restore.db")
                zf.extract("pxbot.db", ROOT)
                return True
    except Exception:
        return False
    return False

# ============================================================
# PANEL CONNECTORS (simplified)
# ============================================================
class BasePanel:
    def __init__(self, base_url: str, username: str = None, password: str = None, token: str = None):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.token = token

    async def test_connection(self) -> tuple[bool, str]:
        return False, "Not implemented"

    async def create_user(self, username: str, data_limit_gb: float = 0, expire_days: int = 30, note: str = "") -> dict:
        return {"username": username, "subscription_url": f"{self.base_url}/sub/{username}"}

    async def close(self):
        pass

class MarzbanPanel(BasePanel):
    async def test_connection(self) -> tuple[bool, str]:
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=15, verify=False) as client:
                r = await client.post("/api/admin/token", data={"username": self.username, "password": self.password})
                if r.status_code == 200:
                    return True, "✅ اتصال به مرزبان موفق"
                return False, f"❌ کد: {r.status_code}"
        except Exception as e:
            return False, f"❌ {str(e)[:100]}"

    async def create_user(self, username: str, data_limit_gb: float = 0, expire_days: int = 30, note: str = "") -> dict:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=20, verify=False) as client:
            r = await client.post("/api/admin/token", data={"username": self.username, "password": self.password})
            token = r.json().get("access_token")
            headers = {"Authorization": f"Bearer {token}"}
            expire = 0 if expire_days <= 0 else int(time.time()) + expire_days * 86400
            data_limit = 0 if data_limit_gb <= 0 else int(data_limit_gb * 1024**3)
            payload = {
                "username": username,
                "proxies": {"vless": {"id": str(uuid.uuid4()), "flow": "xtls-rprx-vision"}},
                "inbounds": {},
                "expire": expire,
                "data_limit": data_limit,
                "status": "active",
                "note": note or "PX Bot",
            }
            r = await client.post("/api/user", json=payload, headers=headers)
            if r.status_code in (200, 201):
                data = r.json()
                return {"username": username, "subscription_url": data.get("subscription_url"), "raw": data}
            raise Exception(r.text[:150])

class PasarGuardPanel(BasePanel):
    async def test_connection(self) -> tuple[bool, str]:
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=15) as client:
                for path in ["/api/admin/token", "/api/token"]:
                    try:
                        r = await client.post(path, data={"username": self.username, "password": self.password})
                        if r.status_code == 200:
                            return True, "✅ اتصال به پاسارگارد موفق"
                    except Exception:
                        continue
                return False, "❌ احراز هویت ناموفق"
        except Exception as e:
            return False, f"❌ {str(e)[:100]}"

    async def create_user(self, username: str, data_limit_gb: float = 0, expire_days: int = 30, note: str = "") -> dict:
        return {"username": username, "subscription_url": f"{self.base_url}/sub/{username}"}

class SanaeiPanel(BasePanel):
    async def test_connection(self) -> tuple[bool, str]:
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=15, verify=False) as client:
                r = await client.post("/login", data={"username": self.username, "password": self.password})
                if r.status_code in (200, 303):
                    return True, "✅ اتصال به سنایی موفق"
                return False, f"❌ کد: {r.status_code}"
        except Exception as e:
            return False, f"❌ {str(e)[:100]}"

    async def create_user(self, username: str, data_limit_gb: float = 0, expire_days: int = 30, note: str = "") -> dict:
        return {"username": username, "subscription_url": f"{self.base_url}/sub/{username}"}

def get_panel_instance(panel: Panel) -> BasePanel:
    mapping = {
        "pasarguard": PasarGuardPanel,
        "marzban": MarzbanPanel,
        "sanaei": SanaeiPanel,
    }
    cls = mapping.get(panel.panel_type.lower(), BasePanel)
    return cls(panel.base_url, panel.username, panel.password, panel.token)

# ============================================================
# KEYBOARDS
# ============================================================
def main_menu(is_admin_user: bool = False) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="🛒 خرید کانفیگ", callback_data="shop", style=ButtonStyle.SUCCESS),
        InlineKeyboardButton(text="🧪 کانفیگ تست", callback_data="test_config", style=ButtonStyle.PRIMARY),
    )
    b.row(
        InlineKeyboardButton(text="📦 سفارش‌های من", callback_data="my_orders", style=ButtonStyle.PRIMARY),
        InlineKeyboardButton(text="💬 پشتیبانی", callback_data="support", style=ButtonStyle.PRIMARY),
    )
    b.row(
        InlineKeyboardButton(text="⭐ نظرات", callback_data="reviews", style=ButtonStyle.PRIMARY),
        InlineKeyboardButton(text="📜 قوانین", callback_data="rules", style=ButtonStyle.PRIMARY),
    )
    if is_admin_user:
        b.row(InlineKeyboardButton(text="🛠 پنل ادمین", callback_data="admin_panel", style=ButtonStyle.DANGER))
    return b.as_markup()

def admin_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="📊 آمار", callback_data="admin_stats", style=ButtonStyle.PRIMARY),
        InlineKeyboardButton(text="💰 سفارش‌ها", callback_data="admin_orders", style=ButtonStyle.SUCCESS),
    )
    b.row(
        InlineKeyboardButton(text="🖥 پنل‌ها", callback_data="admin_panels", style=ButtonStyle.PRIMARY),
        InlineKeyboardButton(text="📢 همگانی", callback_data="admin_broadcast", style=ButtonStyle.DANGER),
    )
    b.row(
        InlineKeyboardButton(text="💾 بک‌آپ", callback_data="admin_backup", style=ButtonStyle.SUCCESS),
        InlineKeyboardButton(text="⚙️ تنظیمات", callback_data="admin_settings", style=ButtonStyle.PRIMARY),
    )
    b.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="back_main", style=ButtonStyle.PRIMARY))
    return b.as_markup()

def confirm_rules() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="✅ پذیرش قوانین و ادامه", callback_data="accept_rules", style=ButtonStyle.SUCCESS))
    return b.as_markup()

def products_keyboard(products: list) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for p in products:
        price = f"{int(p.price):,}".replace(",", "٬")
        text = f"{'🧪 ' if p.is_test else '📦 '}{p.name} — {price} تومان"
        b.row(InlineKeyboardButton(text=text, callback_data=f"product_{p.id}",
                                   style=ButtonStyle.SUCCESS if not p.is_test else ButtonStyle.PRIMARY))
    b.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="back_main", style=ButtonStyle.PRIMARY))
    return b.as_markup()

def payment_keyboard(order_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="📤 ارسال رسید پرداخت", callback_data=f"send_receipt_{order_id}", style=ButtonStyle.SUCCESS))
    b.row(InlineKeyboardButton(text="❌ انصراف", callback_data="cancel_order", style=ButtonStyle.DANGER))
    return b.as_markup()

def admin_order_actions(order_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="✅ تایید و ارسال کانفیگ", callback_data=f"approve_order_{order_id}", style=ButtonStyle.SUCCESS),
        InlineKeyboardButton(text="❌ رد", callback_data=f"reject_order_{order_id}", style=ButtonStyle.DANGER),
    )
    return b.as_markup()

def back_button(cb: str = "back_main") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data=cb, style=ButtonStyle.PRIMARY))
    return b.as_markup()

# ============================================================
# BOT HANDLERS
# ============================================================
router = Router()

class OrderStates(StatesGroup):
    waiting_receipt = State()

class SupportStates(StatesGroup):
    waiting_message = State()

class AdminStates(StatesGroup):
    broadcast = State()

async def check_force_join(user_id: int, bot: Bot) -> tuple[bool, list]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(ForceJoin).where(ForceJoin.is_active == True))
        channels = result.scalars().all()
    if not channels:
        return True, []
    missing = []
    for ch in channels:
        try:
            member = await bot.get_chat_member(ch.channel_id, user_id)
            if member.status in ("left", "kicked"):
                missing.append(ch)
        except Exception:
            missing.append(ch)
    return len(missing) == 0, missing

@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot):
    user_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        user = await session.scalar(select(User).where(User.telegram_id == user_id))
        if not user:
            user = User(
                telegram_id=user_id,
                username=message.from_user.username,
                full_name=message.from_user.full_name,
                is_admin=is_admin(user_id),
            )
            session.add(user)
            await session.commit()
    ok, missing = await check_force_join(user_id, bot)
    if not ok:
        text = "🔒 برای استفاده باید در کانال‌های زیر عضو شوید:\n\n"
        for ch in missing:
            text += f"• {ch.channel_title or ch.channel_id}\n"
        text += "\nبعد از عضویت دوباره /start بزنید."
        await message.answer(text)
        return
    rules = await get_setting("rules")
    await message.answer(f"👋 سلام {message.from_user.first_name}!\n\n{rules}", reply_markup=confirm_rules())

@router.callback_query(F.data == "accept_rules")
async def accept_rules(callback: CallbackQuery):
    welcome = await get_setting("welcome_text")
    await callback.message.edit_text(welcome, reply_markup=main_menu(is_admin(callback.from_user.id)))
    await callback.answer()

@router.callback_query(F.data == "back_main")
async def back_main(callback: CallbackQuery):
    welcome = await get_setting("welcome_text")
    await callback.message.edit_text(welcome, reply_markup=main_menu(is_admin(callback.from_user.id)))
    await callback.answer()

@router.callback_query(F.data == "rules")
async def show_rules(callback: CallbackQuery):
    rules = await get_setting("rules")
    await callback.message.edit_text(rules, reply_markup=back_button())
    await callback.answer()

@router.callback_query(F.data == "shop")
async def shop(callback: CallbackQuery):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Product).where(Product.is_active == True, Product.is_test == False).order_by(Product.sort_order)
        )
        products = result.scalars().all()
    if not products:
        await callback.message.edit_text("🛍 هنوز محصولی تعریف نشده.", reply_markup=back_button())
    else:
        await callback.message.edit_text("🛒 لیست پلن‌ها:", reply_markup=products_keyboard(products))
    await callback.answer()

@router.callback_query(F.data == "test_config")
async def test_config(callback: CallbackQuery):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Product).where(Product.is_active == True, Product.is_test == True).limit(5)
        )
        products = result.scalars().all()
    if not products:
        await callback.message.edit_text("🧪 کانفیگ تستی تعریف نشده.", reply_markup=back_button())
    else:
        await callback.message.edit_text("🧪 کانفیگ‌های تست:", reply_markup=products_keyboard(products))
    await callback.answer()

@router.callback_query(F.data.startswith("product_"))
async def select_product(callback: CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split("_")[1])
    async with AsyncSessionLocal() as session:
        product = await session.get(Product, product_id)
        if not product or not product.is_active:
            await callback.answer("محصول یافت نشد", show_alert=True)
            return
        user = await session.scalar(select(User).where(User.telegram_id == callback.from_user.id))
        order = Order(user_id=user.id, product_id=product.id, amount=product.price,
                      status="waiting_receipt" if product.price > 0 else "pending")
        session.add(order)
        await session.commit()
        await session.refresh(order)
        if product.price <= 0:
            await callback.message.edit_text(
                f"✅ درخواست تست ثبت شد.\nکد سفارش: `{order.id}`",
                reply_markup=back_button(),
            )
            for aid in settings.admin_ids_list:
                try:
                    await callback.bot.send_message(
                        aid, f"🧪 تست جدید\nکاربر: {callback.from_user.full_name}\nسفارش: {order.id}",
                        reply_markup=admin_order_actions(order.id),
                    )
                except Exception:
                    pass
            await callback.answer()
            return
        card = await get_setting("card_number")
        owner = await get_setting("card_owner")
        text = (
            f"🛒 سفارش ثبت شد\n\n📦 {product.name}\n💰 {format_price(product.price)}\n"
            f"🆔 `{order.id}`\n\n💳 کارت: `{card}`\nبه نام: {owner}\n\n"
            "بعد از واریز رسید را ارسال کنید."
        )
        await callback.message.edit_text(text, reply_markup=payment_keyboard(order.id))
        await state.set_state(OrderStates.waiting_receipt)
        await state.update_data(order_id=order.id)
    await callback.answer()

@router.callback_query(F.data.startswith("send_receipt_"))
async def ask_receipt(callback: CallbackQuery, state: FSMContext):
    order_id = int(callback.data.split("_")[2])
    await state.set_state(OrderStates.waiting_receipt)
    await state.update_data(order_id=order_id)
    await callback.message.edit_text("📤 عکس یا فایل رسید را ارسال کنید.", reply_markup=back_button("cancel_order"))
    await callback.answer()

@router.message(OrderStates.waiting_receipt, F.photo | F.document)
async def receive_receipt(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    order_id = data.get("order_id")
    if not order_id:
        await message.answer("سفارش نامعتبر.")
        await state.clear()
        return
    file_id = message.photo[-1].file_id if message.photo else message.document.file_id
    async with AsyncSessionLocal() as session:
        order = await session.get(Order, order_id)
        if not order or order.status != "waiting_receipt":
            await message.answer("سفارش یافت نشد.")
            await state.clear()
            return
        order.receipt_file_id = file_id
        order.status = "pending"
        await session.commit()
        product = await session.get(Product, order.product_id)
        user = await session.get(User, order.user_id)
    await message.answer(f"✅ رسید دریافت شد.\nکد: `{order_id}`", reply_markup=main_menu(is_admin(message.from_user.id)))
    await state.clear()
    caption = (
        f"🧾 رسید جدید\nکاربر: {user.full_name}\nآیدی: `{user.telegram_id}`\n"
        f"محصول: {product.name}\nمبلغ: {format_price(order.amount)}\nسفارش: `{order.id}`"
    )
    for aid in settings.admin_ids_list:
        try:
            if message.photo:
                await bot.send_photo(aid, file_id, caption=caption, reply_markup=admin_order_actions(order.id))
            else:
                await bot.send_document(aid, file_id, caption=caption, reply_markup=admin_order_actions(order.id))
        except Exception:
            pass

@router.callback_query(F.data == "cancel_order")
async def cancel_order(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("لغو شد.", reply_markup=main_menu(is_admin(callback.from_user.id)))
    await callback.answer()

@router.callback_query(F.data == "my_orders")
async def my_orders(callback: CallbackQuery):
    async with AsyncSessionLocal() as session:
        user = await session.scalar(select(User).where(User.telegram_id == callback.from_user.id))
        result = await session.execute(select(Order).where(Order.user_id == user.id).order_by(desc(Order.created_at)).limit(10))
        orders = result.scalars().all()
    if not orders:
        text = "📦 هنوز سفارشی ندارید."
    else:
        status_map = {"pending": "⏳", "waiting_receipt": "📤", "approved": "✅", "rejected": "❌"}
        text = "📦 سفارش‌های شما:\n\n"
        for o in orders:
            text += f"#{o.id} {status_map.get(o.status, o.status)} {format_price(o.amount)}\n"
            if o.config_data and o.status == "approved":
                text += f"🔗 {o.config_data[:70]}...\n"
            text += "\n"
    await callback.message.edit_text(text, reply_markup=back_button())
    await callback.answer()

@router.callback_query(F.data == "support")
async def support(callback: CallbackQuery, state: FSMContext):
    text = await get_setting("support_text")
    await callback.message.edit_text(text, reply_markup=back_button())
    await state.set_state(SupportStates.waiting_message)
    await callback.answer()

@router.message(SupportStates.waiting_message)
async def support_message(message: Message, state: FSMContext, bot: Bot):
    await message.answer("✅ پیام شما ثبت شد.", reply_markup=main_menu(is_admin(message.from_user.id)))
    await state.clear()
    for aid in settings.admin_ids_list:
        try:
            await bot.send_message(aid, f"🎫 پیام پشتیبانی\nاز: {message.from_user.full_name}\n\n{message.text}")
        except Exception:
            pass

@router.callback_query(F.data == "reviews")
async def reviews(callback: CallbackQuery):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Review).where(Review.is_approved == True).order_by(desc(Review.created_at)).limit(10))
        revs = result.scalars().all()
    text = "⭐ نظرات مشتریان:\n\n" + ("\n────────\n".join(f"{'⭐'*r.rating}\n{r.comment or ''}" for r in revs) if revs else "هنوز نظری ثبت نشده.")
    await callback.message.edit_text(text, reply_markup=back_button())
    await callback.answer()

# Admin handlers
@router.callback_query(F.data == "admin_panel")
async def admin_panel(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید", show_alert=True)
        return
    await callback.message.edit_text(
        f"🛠 پنل مدیریت PX Bot v{settings.VERSION}\n⏱ آپتایم: {get_uptime()}",
        reply_markup=admin_menu(),
    )
    await callback.answer()

@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    async with AsyncSessionLocal() as session:
        total_users = await session.scalar(select(func.count(User.id))) or 0
        total_orders = await session.scalar(select(func.count(Order.id))) or 0
        approved = await session.scalar(select(func.count(Order.id)).where(Order.status == "approved")) or 0
        pending = await session.scalar(select(func.count(Order.id)).where(Order.status.in_(["pending", "waiting_receipt"]))) or 0
        revenue = await session.scalar(select(func.coalesce(func.sum(Order.amount), 0)).where(Order.status == "approved")) or 0
    text = (
        f"📊 آمار PX Bot\n\n👥 کاربران: {total_users}\n📦 سفارش‌ها: {total_orders}\n"
        f"✅ تایید شده: {approved}\n⏳ در انتظار: {pending}\n💰 درآمد: {format_price(revenue)}\n"
        f"⏱ آپتایم: {get_uptime()}\n📦 نسخه: {settings.VERSION}"
    )
    await callback.message.edit_text(text, reply_markup=back_button("admin_panel"))
    await callback.answer()

@router.callback_query(F.data.startswith("approve_order_"))
async def approve_order(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        return
    order_id = int(callback.data.split("_")[2])
    async with AsyncSessionLocal() as session:
        order = await session.get(Order, order_id)
        if not order or order.status not in ("pending", "waiting_receipt"):
            await callback.answer("قابل تایید نیست", show_alert=True)
            return
        product = await session.get(Product, order.product_id)
        user = await session.get(User, order.user_id)
        config_data = None
        panel_username = generate_username(user.telegram_id, order.id)
        if product.panel_id:
            panel = await session.get(Panel, product.panel_id)
            if panel and panel.is_active:
                try:
                    p = get_panel_instance(panel)
                    created = await p.create_user(panel_username, product.data_limit_gb, product.duration_days, f"Order #{order.id}")
                    config_data = created.get("subscription_url")
                    await p.close()
                except Exception as e:
                    await callback.answer(f"خطا: {str(e)[:80]}", show_alert=True)
                    return
        order.status = "approved"
        order.config_data = config_data or "با ادمین هماهنگ کنید"
        order.panel_username = panel_username
        order.approved_at = datetime.utcnow()
        if product.duration_days > 0:
            order.expires_at = datetime.utcnow() + timedelta(days=product.duration_days)
        await session.commit()
    try:
        await bot.send_message(
            user.telegram_id,
            f"✅ سفارش #{order_id} تایید شد!\n📦 {product.name}\n🔗 `{order.config_data}`",
        )
    except Exception:
        pass
    await callback.answer("تایید شد")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

@router.callback_query(F.data.startswith("reject_order_"))
async def reject_order(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        return
    order_id = int(callback.data.split("_")[2])
    async with AsyncSessionLocal() as session:
        order = await session.get(Order, order_id)
        if order:
            order.status = "rejected"
            user = await session.get(User, order.user_id)
            await session.commit()
            try:
                await bot.send_message(user.telegram_id, f"❌ سفارش #{order_id} رد شد.")
            except Exception:
                pass
    await callback.answer("رد شد")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await callback.message.edit_text("📢 پیام همگانی را بفرستید:", reply_markup=back_button("admin_panel"))
    await state.set_state(AdminStates.broadcast)
    await callback.answer()

@router.message(AdminStates.broadcast)
async def do_broadcast(message: Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user.id):
        return
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User.telegram_id).where(User.is_banned == False))
        ids = [r[0] for r in result.all()]
    ok = fail = 0
    await message.answer(f"ارسال به {len(ids)} کاربر...")
    for uid in ids:
        try:
            await bot.send_message(uid, message.text)
            ok += 1
        except Exception:
            fail += 1
    await message.answer(f"✅ تمام شد\nموفق: {ok} | ناموفق: {fail}", reply_markup=admin_menu())
    await state.clear()

@router.callback_query(F.data == "admin_backup")
async def admin_backup(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    path = create_backup()
    await callback.message.answer_document(FSInputFile(path), caption=f"💾 بک‌آپ\n{path.name}\n⏱ {get_uptime()}")
    await callback.answer()

@router.callback_query(F.data == "admin_settings")
async def admin_settings(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    card = await get_setting("card_number")
    await callback.message.edit_text(
        f"⚙️ تنظیمات\n💳 کارت: `{card or 'تنظیم نشده'}`\n\nاز پنل وب تغییر دهید.",
        reply_markup=back_button("admin_panel"),
    )
    await callback.answer()

@router.callback_query(F.data == "admin_panels")
async def admin_panels(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    async with AsyncSessionLocal() as session:
        panels = (await session.execute(select(Panel))).scalars().all()
    text = "🖥 پنل‌ها:\n\n" + ("\n".join(f"{'✅' if p.last_test_ok else '⚠️'} {p.name} ({p.panel_type})" for p in panels) if panels else "هنوز پنلی نیست.")
    await callback.message.edit_text(text, reply_markup=back_button("admin_panel"))
    await callback.answer()

@router.callback_query(F.data == "admin_orders")
async def admin_orders(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Order).where(Order.status.in_(["pending", "waiting_receipt"])).order_by(desc(Order.created_at)).limit(15)
        )
        orders = result.scalars().all()
    text = "💰 در انتظار:\n\n" + ("\n".join(f"#{o.id} — {format_price(o.amount)} — {o.status}" for o in orders) if orders else "موردی نیست.")
    await callback.message.edit_text(text, reply_markup=back_button("admin_panel"))
    await callback.answer()

# ============================================================
# WEB PANEL (FastAPI) - No Shadow CSS
# ============================================================
CSS = """
@import url('https://fonts.googleapis.com/css2?family=Vazirmatn:wght@300;400;500;600;700&display=swap');
:root{
  --bg:#0b0e14;
  --bg2:#12161f;
  --card:rgba(255,255,255,0.055);
  --card-hover:rgba(255,255,255,0.08);
  --border:rgba(255,255,255,0.10);
  --border-soft:rgba(255,255,255,0.06);
  --text:#e8edf5;
  --muted:#8b95a8;
  --primary:#5b9dff;
  --success:#3ecf8e;
  --danger:#ff5c5c;
  --radius:18px;
  --gap:20px;
}
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{
  font-family:'Vazirmatn',system-ui,-apple-system,sans-serif;
  background:var(--bg);
  color:var(--text);
  min-height:100vh;
  line-height:1.7;
  background-image:
    radial-gradient(ellipse 80% 50% at 10% -10%,rgba(91,157,255,0.12) 0%,transparent 55%),
    radial-gradient(ellipse 60% 40% at 90% 110%,rgba(62,207,142,0.08) 0%,transparent 50%),
    radial-gradient(ellipse 40% 30% at 50% 50%,rgba(255,255,255,0.02) 0%,transparent 70%);
  background-attachment:fixed;
}
a{color:var(--primary);text-decoration:none}
a:hover{opacity:0.85}
.container{
  max-width:1060px;
  margin:0 auto;
  padding:32px 28px 48px;
}
/* Glass cards */
.card{
  background:var(--card);
  backdrop-filter:blur(20px) saturate(160%);
  -webkit-backdrop-filter:blur(20px) saturate(160%);
  border:1px solid var(--border);
  border-radius:var(--radius);
  padding:28px;
  margin-bottom:var(--gap);
  transition:background 0.2s ease,border-color 0.2s ease;
}
.card:hover{background:var(--card-hover);border-color:rgba(255,255,255,0.14)}
h1{font-size:1.65rem;font-weight:700;margin-bottom:8px;letter-spacing:-0.02em}
h2{font-size:1.15rem;font-weight:600;margin-bottom:16px}
.muted{color:var(--muted);font-size:0.9rem}
/* Navbar glass */
.nav{
  display:flex;flex-wrap:wrap;gap:14px;align-items:center;justify-content:space-between;
  padding:16px 22px;margin-bottom:28px;
  background:rgba(255,255,255,0.05);
  backdrop-filter:blur(24px) saturate(180%);
  -webkit-backdrop-filter:blur(24px) saturate(180%);
  border:1px solid var(--border);
  border-radius:var(--radius);
}
.nav-links{display:flex;flex-wrap:wrap;gap:8px}
.nav a,.btn{
  display:inline-flex;align-items:center;gap:6px;
  padding:9px 16px;border-radius:12px;
  border:1px solid var(--border-soft);
  background:rgba(255,255,255,0.04);
  color:var(--text);font-size:0.88rem;cursor:pointer;
  text-decoration:none;font-family:inherit;
  transition:all 0.18s ease;
}
.nav a:hover,.btn:hover{background:rgba(255,255,255,0.10);border-color:var(--border);text-decoration:none}
.btn-primary{background:rgba(91,157,255,0.14);border-color:rgba(91,157,255,0.30);color:var(--primary)}
.btn-success{background:rgba(62,207,142,0.14);border-color:rgba(62,207,142,0.30);color:var(--success)}
.btn-danger{background:rgba(255,92,92,0.12);border-color:rgba(255,92,92,0.28);color:var(--danger)}
/* Stats */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:var(--gap);margin-bottom:var(--gap)}
.stat{
  background:var(--card);
  backdrop-filter:blur(18px);
  -webkit-backdrop-filter:blur(18px);
  border:1px solid var(--border);
  border-radius:var(--radius);
  padding:24px 18px;text-align:center;
}
.stat .value{font-size:1.85rem;font-weight:700;color:var(--primary);letter-spacing:-0.03em}
.stat .label{color:var(--muted);font-size:0.84rem;margin-top:8px}
/* Forms */
form{display:flex;flex-direction:column;gap:16px}
label{font-size:0.84rem;color:var(--muted);margin-bottom:6px;display:block}
input,textarea,select{
  width:100%;padding:13px 16px;border-radius:12px;
  border:1px solid var(--border);background:rgba(0,0,0,0.30);
  color:var(--text);font-family:inherit;font-size:0.95rem;
  transition:border-color 0.18s ease;
}
input:focus,textarea:focus,select:focus{outline:none;border-color:var(--primary)}
textarea{min-height:130px;resize:vertical}
/* Tables */
table{width:100%;border-collapse:collapse}
th,td{padding:14px 12px;text-align:right;border-bottom:1px solid var(--border-soft)}
th{color:var(--muted);font-weight:500;font-size:0.83rem}
/* Badges */
.badge{display:inline-block;padding:4px 11px;border-radius:20px;font-size:0.75rem;font-weight:500}
.badge-success{background:rgba(62,207,142,0.15);color:var(--success)}
.badge-danger{background:rgba(255,92,92,0.15);color:var(--danger)}
.badge-primary{background:rgba(91,157,255,0.15);color:var(--primary)}
/* Alerts */
.alert{padding:14px 18px;border-radius:12px;margin-bottom:18px;border:1px solid}
.alert-success{background:rgba(62,207,142,0.08);border-color:rgba(62,207,142,0.25);color:var(--success)}
footer{text-align:center;padding:40px 16px 20px;color:var(--muted);font-size:0.85rem}
/* Spacing utilities */
.mb-0{margin-bottom:0}.mb-1{margin-bottom:8px}.mb-2{margin-bottom:16px}.mb-3{margin-bottom:24px}
@media(max-width:640px){
  .container{padding:20px 16px 36px}
  .card{padding:20px}
  .nav{padding:14px 16px}
  .stats{grid-template-columns:1fr 1fr;gap:14px}
}
"""

def render_page(title: str, body: str, version: str = "1.0.0", show_nav: bool = True) -> str:
    nav = ""
    if show_nav:
        nav = f"""
        <nav class="nav">
          <div style="font-weight:700">⚡ PX Bot <span class="muted">v{version}</span></div>
          <div class="nav-links">
            <a href="/dashboard">داشبورد</a>
            <a href="/products">محصولات</a>
            <a href="/panels">پنل‌ها</a>
            <a href="/forcejoin">عضویت اجباری</a>
            <a href="/settings">تنظیمات</a>
            <a href="/backup">بک‌آپ</a>
            <a href="/logout" class="btn-danger">خروج</a>
          </div>
        </nav>"""
    return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · PX Bot</title>
<style>{CSS}</style>
</head>
<body>
<div class="container">
{nav}
{body}
<footer>PX Bot v{version} · مینیمال · بدون سایه</footer>
</div>
</body></html>"""

app = FastAPI(title="PX Bot", version=settings.VERSION)
app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY)

def require_admin(request: Request):
    return bool(request.session.get("admin"))

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not settings.is_configured and not request.session.get("setup_done"):
        return RedirectResponse("/setup")
    if not require_admin(request):
        return RedirectResponse("/login")
    return RedirectResponse("/dashboard")

@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    body = """
    <div class="card" style="max-width:520px;margin:40px auto">
      <h1>🚀 فعال‌سازی PX Bot</h1>
      <p class="muted" style="margin-bottom:20px">توکن ربات و آیدی عددی ادمین‌ها (هر خط یک آیدی)</p>
      <form method="post" action="/setup">
        <div><label>توکن ربات</label><input name="bot_token" required dir="ltr" placeholder="123456:ABC..."></div>
        <div><label>آیدی ادمین‌ها</label><textarea name="admin_ids" required dir="ltr" placeholder="123456789"></textarea></div>
        <button type="submit" class="btn btn-success">فعال‌سازی</button>
      </form>
    </div>"""
    return HTMLResponse(render_page("فعال‌سازی", body, settings.VERSION, show_nav=False))

@app.post("/setup")
async def setup_submit(request: Request, bot_token: str = Form(...), admin_ids: str = Form(...)):
    env_path = ROOT / ".env"
    env_path.write_text(
        f"BOT_TOKEN={bot_token.strip()}\nADMIN_IDS={admin_ids.strip()}\n"
        f"SECRET_KEY={secrets.token_hex(32)}\nDATABASE_URL=sqlite+aiosqlite:///./pxbot.db\nVERSION=1.0.0\n",
        encoding="utf-8",
    )
    request.session["setup_done"] = True
    request.session["admin"] = True
    reload_settings()
    logger.info("Setup complete — BOT_TOKEN loaded, bot will start shortly")
    return RedirectResponse("/dashboard?setup=1", status_code=303)

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    body = """
    <div class="card" style="max-width:420px;margin:60px auto">
      <h1>🔐 ورود</h1>
      <form method="post" action="/login">
        <div><label>رمز عبور</label><input type="password" name="password" required dir="ltr"></div>
        <p class="muted" style="font-size:.8rem">پیش‌فرض: pxadmin</p>
        <button type="submit" class="btn btn-primary">ورود</button>
      </form>
    </div>"""
    return HTMLResponse(render_page("ورود", body, settings.VERSION, show_nav=False))

@app.post("/login")
async def login(request: Request, password: str = Form(...)):
    if password in ("pxadmin", settings.SECRET_KEY[:16]):
        request.session["admin"] = True
        return RedirectResponse("/dashboard", status_code=303)
    body = """<div class="card" style="max-width:420px;margin:60px auto"><h1>🔐 ورود</h1>
    <div class="alert" style="border-color:rgba(248,81,73,.3);color:var(--danger)">رمز اشتباه</div>
    <form method="post" action="/login"><div><label>رمز</label><input type="password" name="password" required></div>
    <button type="submit" class="btn btn-primary">ورود</button></form></div>"""
    return HTMLResponse(render_page("ورود", body, settings.VERSION, show_nav=False))

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login")

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    if not require_admin(request):
        return RedirectResponse("/login")
    async with AsyncSessionLocal() as session:
        total_users = await session.scalar(select(func.count(User.id))) or 0
        total_orders = await session.scalar(select(func.count(Order.id))) or 0
        pending = await session.scalar(select(func.count(Order.id)).where(Order.status.in_(["pending", "waiting_receipt"]))) or 0
        revenue = await session.scalar(select(func.coalesce(func.sum(Order.amount), 0)).where(Order.status == "approved")) or 0
    body = f"""
    <h1>📊 داشبورد</h1>
    <p class="muted" style="margin-bottom:20px">آپتایم: {get_uptime()} · CPU: {psutil.cpu_percent()}% · RAM: {psutil.virtual_memory().percent}%</p>
    <div class="stats">
      <div class="stat"><div class="value">{total_users}</div><div class="label">کاربران</div></div>
      <div class="stat"><div class="value">{total_orders}</div><div class="label">سفارش‌ها</div></div>
      <div class="stat"><div class="value">{pending}</div><div class="label">در انتظار</div></div>
      <div class="stat"><div class="value">{int(revenue):,}</div><div class="label">درآمد (تومان)</div></div>
    </div>
    <div class="card"><h2>خوش آمدید</h2>
    <p>از منو محصولات، پنل‌ها (پاسارگارد/مرزبان/سنایی)، عضویت اجباری و بک‌آپ را مدیریت کنید.</p>
    <p class="muted" style="margin-top:12px">دکمه‌های ربات رنگی هستند · سایت بدون سایه طراحی شده.</p></div>"""
    return HTMLResponse(render_page("داشبورد", body, settings.VERSION))

@app.get("/products", response_class=HTMLResponse)
async def products_page(request: Request):
    if not require_admin(request):
        return RedirectResponse("/login")
    async with AsyncSessionLocal() as session:
        products = (await session.execute(select(Product).order_by(Product.sort_order))).scalars().all()
        panels = (await session.execute(select(Panel))).scalars().all()
    options = "".join(f'<option value="{p.id}">{p.name} ({p.panel_type})</option>' for p in panels)
    rows = "".join(
        f"<tr><td>{p.name}</td><td>{int(p.price):,}</td><td>{p.duration_days} روز</td>"
        f"<td>{'🧪' if p.is_test else '—'}</td>"
        f"<td><span class='badge {'badge-success' if p.is_active else 'badge-danger'}'>{'فعال' if p.is_active else 'غیرفعال'}</span></td></tr>"
        for p in products
    ) or "<tr><td colspan='5' class='muted'>محصولی نیست</td></tr>"
    body = f"""
    <h1>🛍 محصولات</h1>
    <div class="card"><h2>افزودن</h2>
    <form method="post" action="/products/add">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
        <div><label>نام</label><input name="name" required></div>
        <div><label>قیمت</label><input name="price" type="number" required></div>
        <div><label>مدت (روز)</label><input name="duration_days" type="number" value="30"></div>
        <div><label>حجم GB</label><input name="data_limit_gb" type="number" step="0.1" value="0"></div>
        <div><label>پنل</label><select name="panel_id"><option value="">—</option>{options}</select></div>
        <div><label>تست؟</label><select name="is_test"><option value="false">خیر</option><option value="true">بله</option></select></div>
      </div>
      <div><label>توضیحات</label><textarea name="description"></textarea></div>
      <button class="btn btn-success">افزودن</button>
    </form></div>
    <div class="card"><h2>لیست</h2><table>
    <thead><tr><th>نام</th><th>قیمت</th><th>مدت</th><th>تست</th><th>وضعیت</th></tr></thead>
    <tbody>{rows}</tbody></table></div>"""
    return HTMLResponse(render_page("محصولات", body, settings.VERSION))

@app.post("/products/add")
async def add_product(request: Request, name: str = Form(...), price: float = Form(...),
                      duration_days: int = Form(30), data_limit_gb: float = Form(0),
                      panel_id: str = Form(""), is_test: str = Form("false"), description: str = Form("")):
    if not require_admin(request):
        return RedirectResponse("/login")
    async with AsyncSessionLocal() as session:
        session.add(Product(
            name=name, price=price, duration_days=duration_days, data_limit_gb=data_limit_gb,
            panel_id=int(panel_id) if panel_id.isdigit() else None,
            is_test=is_test == "true", description=description,
        ))
        await session.commit()
    return RedirectResponse("/products", status_code=303)

@app.get("/panels", response_class=HTMLResponse)
async def panels_page(request: Request):
    if not require_admin(request):
        return RedirectResponse("/login")
    async with AsyncSessionLocal() as session:
        panels = (await session.execute(select(Panel))).scalars().all()
    rows = "".join(
        f"<tr><td>{p.name}</td><td>{p.panel_type}</td><td dir='ltr' style='font-size:.85rem'>{p.base_url}</td>"
        f"<td><span class='badge {'badge-success' if p.last_test_ok else 'badge-danger'}'>{'موفق' if p.last_test_ok else '—'}</span></td>"
        f"<td><form method='post' action='/panels/test/{p.id}' style='display:inline'><button class='btn btn-primary' style='padding:4px 10px;font-size:.8rem'>تست</button></form></td></tr>"
        for p in panels
    ) or "<tr><td colspan='5' class='muted'>پنلی نیست</td></tr>"
    body = f"""
    <h1>🖥 پنل‌ها</h1>
    <p class="muted" style="margin-bottom:16px">پاسارگارد · مرزبان · سنایی</p>
    <div class="card"><h2>افزودن</h2>
    <form method="post" action="/panels/add">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
        <div><label>نام</label><input name="name" required></div>
        <div><label>نوع</label><select name="panel_type" required>
          <option value="pasarguard">پاسارگارد</option><option value="marzban">مرزبان</option><option value="sanaei">سنایی</option>
        </select></div>
        <div style="grid-column:1/-1"><label>URL</label><input name="base_url" required dir="ltr"></div>
        <div><label>یوزرنیم</label><input name="username" dir="ltr"></div>
        <div><label>پسورد</label><input name="password" type="password" dir="ltr"></div>
        <div style="grid-column:1/-1"><label>توکن (اختیاری)</label><input name="token" dir="ltr"></div>
      </div>
      <button class="btn btn-success">افزودن</button>
    </form></div>
    <div class="card"><h2>لیست</h2><table>
    <thead><tr><th>نام</th><th>نوع</th><th>آدرس</th><th>تست</th><th></th></tr></thead>
    <tbody>{rows}</tbody></table></div>"""
    return HTMLResponse(render_page("پنل‌ها", body, settings.VERSION))

@app.post("/panels/add")
async def add_panel(request: Request, name: str = Form(...), panel_type: str = Form(...),
                    base_url: str = Form(...), username: str = Form(""), password: str = Form(""), token: str = Form("")):
    if not require_admin(request):
        return RedirectResponse("/login")
    async with AsyncSessionLocal() as session:
        session.add(Panel(name=name, panel_type=panel_type, base_url=base_url,
                          username=username or None, password=password or None, token=token or None))
        await session.commit()
    return RedirectResponse("/panels", status_code=303)

@app.post("/panels/test/{panel_id}")
async def test_panel(request: Request, panel_id: int):
    if not require_admin(request):
        return RedirectResponse("/login")
    async with AsyncSessionLocal() as session:
        panel = await session.get(Panel, panel_id)
        if panel:
            try:
                inst = get_panel_instance(panel)
                ok, msg = await inst.test_connection()
                panel.last_test_ok = ok
                panel.last_test = datetime.utcnow()
                await session.commit()
                await inst.close()
            except Exception:
                pass
    return RedirectResponse("/panels", status_code=303)

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    if not require_admin(request):
        return RedirectResponse("/login")
    rules = await get_setting("rules")
    card = await get_setting("card_number")
    owner = await get_setting("card_owner")
    welcome = await get_setting("welcome_text")
    body = f"""
    <h1>⚙️ تنظیمات</h1>
    <div class="card">
    <form method="post" action="/settings">
      <div><label>خوش‌آمدگویی</label><textarea name="welcome_text">{welcome}</textarea></div>
      <div><label>قوانین</label><textarea name="rules" style="min-height:180px">{rules}</textarea></div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
        <div><label>شماره کارت</label><input name="card_number" value="{card}" dir="ltr"></div>
        <div><label>به نام</label><input name="card_owner" value="{owner}"></div>
      </div>
      <button class="btn btn-success">ذخیره</button>
    </form></div>"""
    return HTMLResponse(render_page("تنظیمات", body, settings.VERSION))

@app.post("/settings")
async def save_settings(request: Request, rules: str = Form(""), card_number: str = Form(""),
                        card_owner: str = Form(""), welcome_text: str = Form("")):
    if not require_admin(request):
        return RedirectResponse("/login")
    await set_setting("rules", rules)
    await set_setting("card_number", card_number)
    await set_setting("card_owner", card_owner)
    await set_setting("welcome_text", welcome_text)
    return RedirectResponse("/settings", status_code=303)

@app.get("/backup", response_class=HTMLResponse)
async def backup_page(request: Request):
    if not require_admin(request):
        return RedirectResponse("/login")
    backups = sorted(BACKUP_DIR.glob("pxbot_backup_*.zip"), reverse=True)
    blist = "".join(f"<li style='padding:8px 0;border-bottom:1px solid var(--border)'>{b.name}</li>" for b in backups) or "<li class='muted'>بک‌آپی نیست</li>"
    body = f"""
    <h1>💾 بک‌آپ</h1>
    <p class="muted">آپتایم: {get_uptime()}</p>
    <div class="card"><h2>ایجاد بک‌آپ</h2>
    <form method="post" action="/backup/create"><button class="btn btn-success">دانلود بک‌آپ</button></form></div>
    <div class="card"><h2>ریستور</h2>
    <form method="post" action="/backup/restore" enctype="multipart/form-data">
      <input type="file" name="file" accept=".zip" required>
      <button class="btn btn-danger" style="margin-top:10px">ریستور</button>
    </form></div>
    <div class="card"><h2>بک‌آپ‌های موجود</h2><ul style="list-style:none">{blist}</ul></div>"""
    return HTMLResponse(render_page("بک‌آپ", body, settings.VERSION))

@app.post("/backup/create")
async def create_backup_route(request: Request):
    if not require_admin(request):
        return RedirectResponse("/login")
    path = create_backup()
    return FileResponse(path, filename=path.name)

@app.post("/backup/restore")
async def restore_backup_route(request: Request, file: UploadFile = File(...)):
    if not require_admin(request):
        return RedirectResponse("/login")
    dest = BACKUP_DIR / file.filename
    dest.write_bytes(await file.read())
    restore_backup(dest)
    return RedirectResponse("/backup", status_code=303)

@app.get("/forcejoin", response_class=HTMLResponse)
async def forcejoin_page(request: Request):
    if not require_admin(request):
        return RedirectResponse("/login")
    async with AsyncSessionLocal() as session:
        items = (await session.execute(select(ForceJoin))).scalars().all()
    rows = "".join(
        f"<tr><td>{i.channel_title or '—'}</td><td dir='ltr'>{i.channel_id}</td>"
        f"<td><span class='badge {'badge-success' if i.is_active else 'badge-danger'}'>{'فعال' if i.is_active else 'غیرفعال'}</span></td></tr>"
        for i in items
    ) or "<tr><td colspan='3' class='muted'>کانالی نیست</td></tr>"
    body = f"""
    <h1>🔒 عضویت اجباری</h1>
    <p class="muted" style="margin-bottom:16px">بات باید ادمین کانال باشد</p>
    <div class="card"><h2>افزودن</h2>
    <form method="post" action="/forcejoin/add">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
        <div><label>آیدی کانال</label><input name="channel_id" required dir="ltr"></div>
        <div><label>عنوان</label><input name="channel_title"></div>
      </div>
      <button class="btn btn-success">افزودن</button>
    </form></div>
    <div class="card"><h2>لیست</h2><table>
    <thead><tr><th>عنوان</th><th>آیدی</th><th>وضعیت</th></tr></thead>
    <tbody>{rows}</tbody></table></div>"""
    return HTMLResponse(render_page("عضویت اجباری", body, settings.VERSION))

@app.post("/forcejoin/add")
async def add_forcejoin(request: Request, channel_id: str = Form(...), channel_title: str = Form("")):
    if not require_admin(request):
        return RedirectResponse("/login")
    async with AsyncSessionLocal() as session:
        session.add(ForceJoin(channel_id=channel_id.strip(), channel_title=channel_title or None))
        await session.commit()
    return RedirectResponse("/forcejoin", status_code=303)

# ============================================================
# START
# ============================================================
async def run_bot():
    """Start bot with polling. Waits until BOT_TOKEN is available (after /setup)."""
    while True:
        reload_settings()
        if settings.BOT_TOKEN:
            break
        logger.warning("BOT_TOKEN not set — waiting... configure via /setup then restart or wait")
        await asyncio.sleep(8)

    bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    logger.info("PX Bot v%s starting (polling) | Admins: %s", settings.VERSION, settings.admin_ids_list)
    try:
        # Delete webhook if any (important for polling)
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    except Exception as e:
        logger.error("Bot error: %s", e)
        await asyncio.sleep(5)
        raise

async def run_web():
    port = int(os.environ.get("PORT", settings.WEB_PORT))
    config = uvicorn.Config(app, host=settings.WEB_HOST, port=port, log_level="info")
    server = uvicorn.Server(config)
    logger.info("Web panel on http://%s:%s", settings.WEB_HOST, port)
    await server.serve()

async def main():
    await init_db()
    logger.info("PX Bot v%s ready | Flat version", settings.VERSION)
    await asyncio.gather(run_web(), run_bot())

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bye")
