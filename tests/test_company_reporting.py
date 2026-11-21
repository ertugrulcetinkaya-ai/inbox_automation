"""Offline tests for the optional Company Reporting integration."""

from __future__ import annotations

import builtins
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mail_digest.integrations import company_reporting


def fake_package_modules(module_name, module):
    app = types.ModuleType("app")
    app.__path__ = []
    company_reports = types.ModuleType("app.company_reports")
    company_reports.__path__ = []
    return {
        "app": app,
        "app.company_reports": company_reports,
        module_name: module,
    }


class CompanyReportingIntegrationTests(unittest.TestCase):
    def test_report_command_works_when_optional_package_is_available(self):
        router = types.ModuleType("app.company_reports.live_command_router")
        error_type = type("CompanyReportCommandError", (Exception,), {})
        dispatch = Mock(return_value=SimpleNamespace(response="Report output", matched=True))
        router.CompanyReportCommandError = error_type
        router.dispatch_company_report_command = dispatch

        with patch.dict(sys.modules, fake_package_modules(
            "app.company_reports.live_command_router", router
        )):
            result = company_reporting.run_company_report_command("/servis")

        dispatch.assert_called_once_with("/servis", send=True)
        self.assertEqual(
            result,
            {"ok": True, "response": "Report output", "matched": True},
        )

    def test_report_command_keeps_unavailable_message_when_package_is_missing(self):
        original_import = builtins.__import__

        def missing_reporting(name, *args, **kwargs):
            if name == "app.company_reports.live_command_router":
                raise ModuleNotFoundError(name)
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=missing_reporting):
            result = company_reporting.run_company_report_command("/fatura")

        self.assertEqual(result, company_reporting.REPORT_SERVICE_UNAVAILABLE)

    def test_report_command_formats_known_command_error(self):
        router = types.ModuleType("app.company_reports.live_command_router")
        error_type = type("CompanyReportCommandError", (Exception,), {})
        dispatch = Mock(side_effect=error_type("fixture report error"))
        router.CompanyReportCommandError = error_type
        router.dispatch_company_report_command = dispatch

        with patch.dict(sys.modules, fake_package_modules(
            "app.company_reports.live_command_router", router
        )):
            result = company_reporting.run_company_report_command("/servis")

        self.assertEqual(result, "Rapor üretilemedi: fixture report error")

    def test_session_status_reports_ready_and_unavailable_states(self):
        worker_module = types.ModuleType("app.company_reports.portal_browser_worker")

        class ReadyWorker:
            async def session_ready(self):
                return True

        worker_module.PortalBrowserWorker = SimpleNamespace(instance=lambda: ReadyWorker())
        with patch.dict(sys.modules, fake_package_modules(
            "app.company_reports.portal_browser_worker", worker_module
        )):
            self.assertEqual(
                company_reporting.run_report_session_status(), "✅ SYS oturumu aktif."
            )

        original_import = builtins.__import__

        def missing_worker(name, *args, **kwargs):
            if name == "app.company_reports.portal_browser_worker":
                raise ModuleNotFoundError(name)
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=missing_worker):
            self.assertEqual(
                company_reporting.run_report_session_status(),
                "⚠️ SYS oturumu yok / MFA gerekiyor.",
            )

    def test_session_preparation_reports_ready_pending_and_error(self):
        worker_module = types.ModuleType("app.company_reports.portal_browser_worker")
        worker = Mock()
        worker.prepare_and_check_session.side_effect = [
            {"session_ready": True},
            {"session_ready": False},
            RuntimeError("fixture setup error"),
        ]
        worker_module.PortalBrowserWorker = SimpleNamespace(instance=lambda: worker)

        with patch.dict(sys.modules, fake_package_modules(
            "app.company_reports.portal_browser_worker", worker_module
        )):
            self.assertEqual(
                company_reporting.run_prepare_report_session(), "✅ SYS oturumu hazır."
            )
            self.assertEqual(
                company_reporting.run_prepare_report_session(),
                "⚠️ SYS oturumu yok / MFA gerekiyor.",
            )
            self.assertEqual(
                company_reporting.run_prepare_report_session(),
                "SYS oturumu hazırlanamadi: fixture setup error",
            )

        self.assertTrue(all(call.kwargs == {"headed": True} for call in worker.prepare_and_check_session.call_args_list))


if __name__ == "__main__":
    unittest.main()
