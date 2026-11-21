"""Lazy bridge to the optional Company Reporting package."""

import asyncio


REPORT_SERVICE_UNAVAILABLE = (
    "Rapor servisi bu ortamda hazır değil; toplantı komutları yine kullanılabilir."
)


def run_company_report_command(text):
    try:
        from app.company_reports.live_command_router import (
            CompanyReportCommandError,
            dispatch_company_report_command,
        )
    except ImportError:
        return REPORT_SERVICE_UNAVAILABLE

    try:
        result = dispatch_company_report_command(text, send=True)
    except CompanyReportCommandError as exc:
        return f"Rapor üretilemedi: {str(exc)[:200] or 'unknown error'}"
    return {"ok": True, "response": result.response, "matched": result.matched}


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
