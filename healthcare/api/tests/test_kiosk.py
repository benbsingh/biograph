# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# See license.txt

import contextlib
import types

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

from healthcare.api import kiosk_common
from healthcare.kiosk_setup import KIOSK_DOCTYPES, KIOSK_ROLE, ensure_kiosk_role_and_permissions

PERM_TYPES = ("read", "write", "create", "delete", "submit", "cancel", "amend", "report", "export", "import", "share", "print", "email", "select")


KIOSK_USER = "kiosk-test@example.com"
STAFF_USER = "kiosk-staff-test@example.com"


@contextlib.contextmanager
def session_id(sid):
	previous = frappe.session.sid
	frappe.session["sid"] = sid
	try:
		yield
	finally:
		frappe.session["sid"] = previous


class as_admin(contextlib.ContextDecorator):
	def __enter__(self):
		self.user = frappe.session.user
		frappe.set_user("Administrator")

	def __exit__(self, *exc):
		frappe.set_user(self.user)


@as_admin()
def make_user(email, roles):
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": "KioskTest", "send_welcome_email": 0, "enabled": 1}
		).insert(ignore_permissions=True)
	user = frappe.get_doc("User", email)
	user.add_roles(*roles)
	return user


@as_admin()
def make_camp(**kwargs):
	"""An Ongoing camp running today, with the departments Medical Camp requires."""
	for name in ("_Test Camp Cardiology",):
		if not frappe.db.exists("Medical Department", name):
			frappe.get_doc({"doctype": "Medical Department", "department": name}).insert()
	doc = {
		"doctype": "Medical Camp",
		"camp_name": "_Test Kiosk Camp",
		"status": "Ongoing",
		"company": frappe.defaults.get_defaults().company,
		"start_date": nowdate(),
		"end_date": add_days(nowdate(), 1),
		"specialities": [{"medical_department": "_Test Camp Cardiology"}],
		**kwargs,
	}
	return frappe.get_doc(doc).insert()


class KioskTestCase(IntegrationTestCase):
	"""Calls the whitelisted functions directly as the shared Kiosk user, with the kiosk switched on."""

	def setUp(self):
		ensure_kiosk_role_and_permissions()
		make_user(KIOSK_USER, [KIOSK_ROLE])
		make_user(STAFF_USER, ["Physician"])
		frappe.cache.delete_keys("abdm:")
		frappe.cache.delete_keys("kiosk:")
		self.configure()
		frappe.set_user(KIOSK_USER)
		frappe.local.response.pop("http_status_code", None)

	def tearDown(self):
		frappe.set_user("Administrator")

	def configure(self, **kwargs):
		user = frappe.session.user
		frappe.set_user("Administrator")
		settings = frappe.get_single("Kiosk Settings")
		settings.update(
			{
				"enabled": 1,
				"patient_source": "Walk-in",
				"medical_camp": None,
				"idle_timeout": 60,
				"enable_abha": 0,
				"enable_appointment": 0,
				**kwargs,
			}
		)
		settings.save(ignore_permissions=True)
		frappe.set_user(user)


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


class TestKioskGuards(KioskTestCase):
	def endpoint(self):
		@kiosk_common.kiosk_endpoint
		def ok():
			return "ok"

		return ok

	def test_guest_denied(self):
		frappe.set_user("Guest")
		self.assertRaises(frappe.PermissionError, self.endpoint())

	def test_other_role_denied(self):
		frappe.set_user(STAFF_USER)
		self.assertRaises(frappe.PermissionError, self.endpoint())

	def test_system_manager_without_kiosk_role_denied(self):
		make_user(STAFF_USER, ["System Manager"])
		frappe.set_user(STAFF_USER)
		self.assertNotIn(KIOSK_ROLE, frappe.get_roles())
		self.assertRaises(frappe.PermissionError, self.endpoint())

	def test_kiosk_user_allowed(self):
		self.assertEqual(self.endpoint()(), "ok")

	def test_disabled(self):
		self.configure(enabled=0)
		with self.assertRaises(frappe.ValidationError) as ctx:
			self.endpoint()()
		self.assertIn("Kiosk unavailable", str(ctx.exception))

	def test_generic_error_hides_internals(self):
		@kiosk_common.kiosk_endpoint
		def boom():
			raise RuntimeError("secret table tabPatient exploded for 9876543210")

		with self.assertRaises(frappe.ValidationError) as ctx:
			boom()
		self.assertNotIn("secret", str(ctx.exception))
		self.assertNotIn("9876543210", str(ctx.exception))

	def test_known_errors_pass_through(self):
		@kiosk_common.kiosk_endpoint
		def invalid():
			frappe.throw("Mobile number is not valid", frappe.ValidationError)

		with self.assertRaises(frappe.ValidationError) as ctx:
			invalid()
		self.assertIn("Mobile number is not valid", str(ctx.exception))

	def test_post_only(self):
		frappe.local.request = types.SimpleNamespace(method="GET")
		try:
			self.assertRaises(Exception, self.endpoint())
		finally:
			frappe.local.request = None

	def test_rate_limit_sets_429(self):
		for _ in range(10):
			kiosk_common.throttle("kiosk_test")
		with self.assertRaises(frappe.ValidationError):
			kiosk_common.throttle("kiosk_test")
		self.assertEqual(frappe.local.response.get("http_status_code"), 429)

	def test_elevated_restores_user(self):
		with kiosk_common.elevated():
			self.assertEqual(frappe.session.user, "Administrator")
		self.assertEqual(frappe.session.user, KIOSK_USER)

	def test_elevated_restores_user_on_error(self):
		with self.assertRaises(ValueError):
			with kiosk_common.elevated():
				raise ValueError
		self.assertEqual(frappe.session.user, KIOSK_USER)

	def test_session_binding(self):
		with session_id("sid-A"):
			kiosk_common.bind_patient("PAT-1", created=False)
			kiosk_common.assert_bound("PAT-1")
			self.assertRaises(frappe.PermissionError, kiosk_common.assert_bound, "PAT-1", True)
			kiosk_common.bind_patient("PAT-1", created=True)
			kiosk_common.assert_bound("PAT-1", require_created=True)
			kiosk_common.bind_txn("TXN-1", "PAT-1")
			kiosk_common.assert_txn_bound("TXN-1", "PAT-1")
			self.assertRaises(frappe.PermissionError, kiosk_common.assert_txn_bound, "TXN-1", "PAT-2")
		with session_id("sid-B"):
			self.assertRaises(frappe.PermissionError, kiosk_common.assert_bound, "PAT-1")
			self.assertRaises(frappe.PermissionError, kiosk_common.assert_txn_bound, "TXN-1", "PAT-1")

	def test_clear_session(self):
		kiosk_common.bind_patient("PAT-1", created=True)
		kiosk_common.clear_session()
		self.assertRaises(frappe.PermissionError, kiosk_common.assert_bound, "PAT-1")

	def test_camp_must_be_ongoing_and_in_range(self):
		camp = make_camp()
		self.configure(patient_source="Medical Camp", medical_camp=camp.name)
		self.assertEqual(kiosk_common.get_active_camp(kiosk_common.get_settings()).name, camp.name)
		past = {"start_date": add_days(nowdate(), -3), "end_date": add_days(nowdate(), -2)}
		for change in ({"status": "Completed"}, {"status": "Planned"}, past):
			dates = {"start_date": nowdate(), "end_date": add_days(nowdate(), 1)}
			frappe.db.set_value("Medical Camp", camp.name, {"status": "Ongoing", **dates, **change})
			with self.assertRaises(frappe.ValidationError):
				kiosk_common.get_active_camp(kiosk_common.get_settings())

	def test_walk_in_has_no_camp(self):
		self.assertIsNone(kiosk_common.get_active_camp(kiosk_common.get_settings()))
