# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from healthcare.kiosk_setup import KIOSK_ROLE, KIOSK_DOCTYPES, ensure_kiosk_role_and_permissions

PERM_TYPES = ("read", "write", "create", "delete", "submit", "cancel", "amend", "report", "export", "import", "share", "print", "email", "select")


def _perm_rows(doctype, role):
	return frappe.get_all("Custom DocPerm", filters={"parent": doctype, "role": role}, fields=["*"])


class TestKioskRole(IntegrationTestCase):
	def setUp(self):
		ensure_kiosk_role_and_permissions()

	def test_role_has_no_desk_access(self):
		self.assertEqual(frappe.db.get_value("Role", KIOSK_ROLE, "desk_access"), 0)

	def test_role_matrix_is_create_only(self):
		for doctype in KIOSK_DOCTYPES:
			rows = _perm_rows(doctype, KIOSK_ROLE)
			self.assertEqual(len(rows), 1, doctype)
			row = rows[0]
			self.assertEqual(row.create, 1)
			for ptype in PERM_TYPES:
				if ptype != "create":
					self.assertFalse(row.get(ptype), f"{doctype}.{ptype}")

	def test_no_permission_on_any_other_doctype(self):
		for table in ("DocPerm", "Custom DocPerm"):
			others = frappe.get_all(
				table, filters={"role": KIOSK_ROLE, "parent": ("not in", KIOSK_DOCTYPES)}, pluck="parent"
			)
			self.assertEqual(others, [], table)

	def test_ensure_is_idempotent(self):
		before = {dt: frappe.db.count("Custom DocPerm", {"parent": dt}) for dt in KIOSK_DOCTYPES}
		ensure_kiosk_role_and_permissions()
		ensure_kiosk_role_and_permissions()
		after = {dt: frappe.db.count("Custom DocPerm", {"parent": dt}) for dt in KIOSK_DOCTYPES}
		self.assertEqual(before, after)
		self.assertEqual(frappe.db.count("Role", {"name": KIOSK_ROLE}), 1)

	def test_other_roles_keep_their_permissions(self):
		# Frappe ignores every standard DocPerm once a single Custom DocPerm exists for the doctype,
		# so a naive insert for Kiosk would strip Physician and friends of their access.
		for doctype in KIOSK_DOCTYPES:
			standard = {
				r.role
				for r in frappe.get_all("DocPerm", filters={"parent": doctype, "permlevel": 0, "read": 1}, fields=["role"])
			}
			effective = {p.role for p in frappe.permissions.get_valid_perms(doctype) if p.read}
			self.assertTrue(standard <= effective, f"{doctype}: lost {standard - effective}")
