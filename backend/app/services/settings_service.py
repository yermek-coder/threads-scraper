"""Read and update persisted runtime settings without exposing secrets."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import SettingsRead, SettingsUpdate
from app.core.config import config
from app.models import Setting

SECRET_KEYS = {"threads_access_token", "openrouter_api_key", "smtp_pass"}
JEV_VISIBLE_RULES = """Уточнения ниже имеют приоритет над противоречащими им формулировками выше.

Язык: публикация должна быть преимущественно на русском или казахском языке.

География: целевой регион — Восточно-Казахстанская область (ВКО), Казахстан. Отклоняй по географии только при явной привязке места работы, оказания услуги, организации, события или описанного опыта к месту вне ВКО или вне Казахстана. Отсутствие города, области или страны не является причиной отказа. Случайное упоминание другого города не считается привязкой. Хештеги, флаг, упоминание Усть-Каменогорска и доставка по Казахстану не отменяют явную нецелевую привязку. Для ответа учитывай также географию обсуждаемого опыта из предыдущих публикаций.

Вакансии: любые объявления о найме, поиске специалистов и приглашении в команду нерелевантны, даже при совпадении организации или профессии с ключевой фразой.

Реклама и самопродвижение: предложения товаров и услуг, акции, скидки, демонстрации «до и после», портфолио и рассказы специалиста или организации об успешном лечении и результатах работы нерелевантны. Цена, контакты и призыв записаться не обязательны. Отзывы клиентов, опубликованные организацией для продвижения, также исключай. Самостоятельный личный отзыв пациента, жалоба или обсуждение осложнений могут быть релевантными. Упоминание услуги, цены или телефона само по себе не делает личный опыт рекламой.

Случайные совпадения слов, фамилий, названий аккаунтов и хештегов без содержательной связи с темой нерелевантны."""

DEFAULT_JEV_RELEVANCE_PROMPT = (
    "Определи, релевантна ли публикация из state.post ключевой фразе из state.keyword. "
    "Основная тема должна содержательно относиться к ключевой фразе, её объекту "
    "или непосредственному контексту.\n\n" + JEV_VISIBLE_RULES
)


async def migrate_jev_visible_prompt(db: AsyncSession) -> None:
    """Move the agreed rules into the editable prompt once; preserve later admin edits."""
    marker = "jev_visible_prompt_v1"
    if await get_setting(db, marker):
        return
    prompt = await get_setting(db, "jev_relevance_prompt")
    if prompt and JEV_VISIBLE_RULES not in prompt:
        await save_setting(db, "jev_relevance_prompt", prompt + "\n\n" + JEV_VISIBLE_RULES)
    elif not prompt:
        await save_setting(db, "jev_relevance_prompt", DEFAULT_JEV_RELEVANCE_PROMPT)
    await save_setting(db, marker, True)
    await db.commit()



def _env_value(key: str) -> Any:
    """Read one setting from environment-backed configuration."""
    value = getattr(config, key, None)
    if value is None:
        return None
    if hasattr(value, "get_secret_value"):
        return value.get_secret_value()
    if key in {"whatsapp_recipients", "email_recipients"}:
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


async def get_setting(db: AsyncSession, key: str) -> Any:
    """Read a setting from the database, falling back to the environment."""
    setting = await db.get(Setting, key)
    return setting.value if setting is not None else _env_value(key)


async def save_setting(db: AsyncSession, key: str, value: Any) -> None:
    """Upsert a setting value in the current transaction."""
    setting = await db.get(Setting, key)
    if setting is None:
        db.add(Setting(key=key, value=value))
    else:
        setting.value = value


async def read_settings(db: AsyncSession) -> SettingsRead:
    """Build a dashboard-safe view of editable integration settings."""
    token = await get_setting(db, "threads_access_token")
    openrouter_key = await get_setting(db, "openrouter_api_key")
    smtp_pass = await get_setting(db, "smtp_pass")
    web_session = await get_setting(db, "threads_web_storage_state")
    return SettingsRead(
        threads_app_id_configured=bool(config.threads_app_id.strip()),
        threads_access_token_configured=bool(token),
        threads_collection_method=await get_setting(db, "threads_collection_method") or "web",
        threads_web_session_configured=isinstance(web_session, dict),
        openrouter_api_key_configured=bool(openrouter_key),
        jev_relevance_enabled=bool(await get_setting(db, "jev_relevance_enabled")),
        jev_relevance_prompt=await get_setting(db, "jev_relevance_prompt") or DEFAULT_JEV_RELEVANCE_PROMPT,
        threads_user_id=await get_setting(db, "threads_user_id"),
        threads_verify_token=config.threads_verify_token.get_secret_value(),
        smtp_host=await get_setting(db, "smtp_host"),
        smtp_port=await get_setting(db, "smtp_port"),
        smtp_user=await get_setting(db, "smtp_user"),
        smtp_pass_configured=bool(smtp_pass),
        smtp_from=await get_setting(db, "smtp_from"),
        smtp_use_tls=await get_setting(db, "smtp_use_tls"),
        whatsapp_recipients=await get_setting(db, "whatsapp_recipients"),
        email_recipients=await get_setting(db, "email_recipients"),
    )


async def update_settings(db: AsyncSession, payload: SettingsUpdate) -> SettingsRead:
    """Persist fields supplied by the user; blank secret fields preserve saved values."""
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is None:
            continue
        if key in SECRET_KEYS and not value.strip():
            continue
        await save_setting(db, key, value)
    await db.commit()
    return await read_settings(db)
