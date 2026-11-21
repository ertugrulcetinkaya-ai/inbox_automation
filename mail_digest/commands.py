"""Telegram aliases for meeting digest commands."""

MEETING_COMMANDS = {
    "/toplantılar": "daily",
    "/toplantilar": "daily",
    "/bugün": "daily",
    "/bugun": "daily",
    "/mail": "daily",
    "/unread": "daily",
    "/gelecek_toplantılar": "upcoming",
    "/gelecek_toplantilar": "upcoming",
    "/toplantılar_gelecek": "upcoming",
    "/toplantilar_gelecek": "upcoming",
    "/sonraki_toplantılar": "upcoming",
    "/sonraki_toplantilar": "upcoming",
    "/hafta": "weekly",
    "/haftalık": "weekly",
    "/haftalik": "weekly",
    "/week": "weekly",
    "/dikkat": "attention",
    "/attention": "attention",
}


_MODE_LABELS = {
    "daily": "Bugünün toplantıları",
    "upcoming": "Bugün ve sonraki toplantılar",
    "weekly": "Haftalık toplantılar",
    "attention": "Dikkat gerektirenler",
}


def meeting_help_text() -> str:
    """Render one help row per mode from the canonical alias table."""

    modes = dict.fromkeys(MEETING_COMMANDS.values())
    lines = ["Toplantı komutları:"]
    for mode in modes:
        aliases = ", ".join(
            alias for alias, alias_mode in MEETING_COMMANDS.items() if alias_mode == mode
        )
        lines.append(f"{_MODE_LABELS[mode]}: {aliases}")
    return "\n".join(lines)
