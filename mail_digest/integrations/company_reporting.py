"""Lazy bridge to the optional Company Reporting package."""

import asyncio


REPORT_SERVICE_UNAVAILABLE = (
    "Rapor servisi bu ortamda hazır değil; toplantı komutları yine kullanılabilir."
)


def is_registered_company_report_command(text):
    try:
        from app.company_reports.live_command_router import (
            parse_company_report_command,
        )
    except ImportError:
        return False

    try:
        return parse_company_report_command(text) is not None
    except Exception:
        return False


def run_company_report_command(text, *, send=True):
    unavailable = {
        "ok": False,
        "response": REPORT_SERVICE_UNAVAILABLE,
        "matched": False,
        "suppress_reply": False,
        "error_code": None,
    }
    try:
        from app.company_reports.live_command_router import (
            CompanyReportCommandError,
            dispatch_company_report_command_async,
        )
    except ImportError:
        return unavailable

    try:
        result = asyncio.run(dispatch_company_report_command_async(text, send=send))
    except CompanyReportCommandError as exc:
        notification_attempted = bool(getattr(exc, "notification_attempted", False))
        return {
            "ok": False,
            "response": None
            if send and notification_attempted
            else f"Rapor üretilemedi: {str(exc)[:200] or 'unknown error'}",
            "matched": True,
            "suppress_reply": send and notification_attempted,
            "error_code": getattr(exc, "error_code", None),
        }
    except Exception as exc:
        notification_attempted = bool(getattr(exc, "notification_attempted", False))
        return {
            "ok": False,
            "response": None
            if send and notification_attempted
            else f"Rapor üretilemedi: {exc.__class__.__name__}",
            "matched": True,
            "suppress_reply": send and notification_attempted,
            "error_code": getattr(exc, "error_code", None),
        }
    return {
        "ok": bool(getattr(result, "ok", False)),
        "response": getattr(result, "response", None),
        "matched": bool(getattr(result, "matched", False)),
        "suppress_reply": bool(getattr(result, "suppress_reply", False)),
        "error_code": getattr(result, "error_code", None),
    }


def run_report_session_status():
    try:
        from app.company_reports.portal_browser_worker import PortalBrowserWorker

        worker = PortalBrowserWorker.instance()
        ready = asyncio.run(worker.session_ready())
    except Exception:
        ready = False
    return "✅ SYS oturumu aktif." if ready else "⚠️ SYS oturumu yok / MFA gerekiyor."


def run_prepare_report_session():
    try:
        from app.company_reports.portal_browser_worker import PortalBrowserWorker

        worker = PortalBrowserWorker.instance()
        result = worker.prepare_and_check_session(headed=True)
        if isinstance(result, dict) and result.get("session_ready"):
            return "✅ SYS oturumu hazır."
        return "⚠️ SYS oturumu yok / MFA gerekiyor."
    except Exception as exc:
        return f"SYS oturumu hazırlanamadi: {str(exc)[:200] or 'unknown error'}"
