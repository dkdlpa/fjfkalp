"""Генераторы HTML-отчётов."""
from app.reports import (
    dork_report,
    email_report,
    ip_domain_report,
    phone_report,
    telegram,
    tiktok_report,
    username_report,
)

__all__ = [
    "telegram",
    "email_report",
    "tiktok_report",
    "username_report",
    "ip_domain_report",
    "phone_report",
    "dork_report",
]