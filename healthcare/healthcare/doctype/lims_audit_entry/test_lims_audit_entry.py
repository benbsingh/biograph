# Copyright (c) 2026, healthcare and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from healthcare.healthcare.doctype.lims_audit_entry.lims_audit_entry import record_audit_entry

IGNORE_TEST_RECORD_DEPENDENCIES = ["DocType", "User"]


class TestLIMSAuditEntry(IntegrationTestCase):
	def _entry(self):
		name = record_audit_entry(
			action="Test Action",
			reference_doctype="User",
			reference_name="Administrator",
			reason="Fixture reason",
			details={"k": "v"},
		)
		return frappe.get_doc("LIMS Audit Entry", name)

	def test_helper_writes_who_what_when(self):
		entry = self._entry()

		self.assertEqual(entry.user, frappe.session.user)
		self.assertEqual(entry.action, "Test Action")
		self.assertEqual(entry.reference_doctype, "User")
		self.assertEqual(entry.reference_name, "Administrator")
		self.assertEqual(entry.reason, "Fixture reason")
		self.assertTrue(entry.occurred_at)
		self.assertIn('"k"', entry.details)

	def test_edit_is_refused(self):
		entry = self._entry()

		entry.reason = "Rewritten"
		with self.assertRaises(frappe.PermissionError):
			entry.save()

		self.assertEqual(frappe.db.get_value("LIMS Audit Entry", entry.name, "reason"), "Fixture reason")

	def test_delete_is_refused(self):
		entry = self._entry()

		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc("LIMS Audit Entry", entry.name, force=True)

		self.assertTrue(frappe.db.exists("LIMS Audit Entry", entry.name))
