# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# See license.txt

import contextlib
import types
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

import json

from healthcare.api import kiosk, kiosk_abha, kiosk_common
from healthcare.www import kiosk as kiosk_page
from healthcare.regional.india.abdm.api import enrol, profile
from healthcare.regional.india.abdm.utils.abha_client import AbhaClient
from healthcare.kiosk_setup import KIOSK_DOCTYPES, KIOSK_ROLE, ensure_kiosk_role_and_permissions

PERM_TYPES = ("read", "write", "create", "delete", "submit", "cancel", "amend", "report", "export", "import", "share", "print", "email", "select")


KIOSK_USER = "kiosk-test@example.com"
STAFF_USER = "kiosk-staff-test@example.com"
CAMP_APPOINTMENT_TYPE = "_Test Kiosk Camp Visit"


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
	if not frappe.db.exists("Appointment Type", CAMP_APPOINTMENT_TYPE):
		frappe.get_doc(
			{"doctype": "Appointment Type", "appointment_type": CAMP_APPOINTMENT_TYPE, "allow_booking_for": "Medical Camp"}
		).insert()
	doc = {
		"doctype": "Medical Camp",
		"camp_name": "_Test Kiosk Camp",
		"appointment_type": CAMP_APPOINTMENT_TYPE,
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
		self.use_leaf_customer_defaults()
		self.configure()
		frappe.set_user(KIOSK_USER)
		frappe.local.response.pop("http_status_code", None)

	def tearDown(self):
		frappe.set_user("Administrator")

	@as_admin()
	def use_leaf_customer_defaults(self):
		# Patient creates a Customer, and Customer refuses the root (group) Customer Group and Territory
		# that Patient falls back to when Selling Settings has none. A real site sets them.
		for fieldname, doctype in (("customer_group", "Customer Group"), ("territory", "Territory")):
			if not frappe.db.get_single_value("Selling Settings", fieldname) or frappe.db.get_value(
				doctype, frappe.db.get_single_value("Selling Settings", fieldname), "is_group"
			):
				leaf = frappe.db.get_value(doctype, {"is_group": 0})
				if leaf:
					frappe.db.set_single_value("Selling Settings", fieldname, leaf)

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


_mobile_counter = [0]


def unique_mobile():
	"""A fresh mobile per call so tests never meet a patient already on the site."""
	_mobile_counter[0] += 1
	return f"98{int(frappe.utils.now_datetime().timestamp()) % 100000:05d}{_mobile_counter[0]:03d}"


def patient_args(**overrides):
	# A fresh name too: Healthcare Settings' duplicate check refuses the same name, gender and DOB
	_mobile_counter[0] += 1
	return {
		"first_name": f"Asha{_mobile_counter[0]}x{int(frappe.utils.now_datetime().timestamp()) % 100000}",
		"last_name": "Verma",
		"sex": "Female",
		"dob": "1990-05-17",
		"mobile": unique_mobile(),
		**overrides,
	}


def patient_count():
	return frappe.db.count("Patient")


RESULT_KEYS = {"uhid", "patient_name", "already_registered", "masked_mobile", "token_number", "token", "camp"}


class TestKioskRegister(KioskTestCase):
	def test_happy_path_returns_only_the_allow_listed_keys(self):
		args = patient_args(email="asha@example.com")
		result = kiosk.register_patient(**args)

		self.assertEqual(set(result), RESULT_KEYS)
		self.assertFalse(result["already_registered"])
		self.assertEqual(result["masked_mobile"], "XXXXXX" + args["mobile"][-4:])
		self.assertTrue(result["patient_name"].startswith("Asha"))
		self.assertTrue(result["patient_name"].endswith("Verma"))
		self.assertTrue(frappe.db.exists("Patient", result["uhid"]))
		self.assertTrue(frappe.db.exists("Kiosk Token", result["token"]))
		self.assertEqual(result["token_number"], int(result["token"].rsplit("-", 1)[1]))
		self.assertIsNone(result["camp"])

	def test_invalid_input_creates_nothing(self):
		bad = [
			{"first_name": ""},
			{"first_name": "   "},
			{"first_name": "<script>alert(1)</script>"},
			{"first_name": "A" * 141},
			{"sex": "Not A Gender"},
			{"sex": ""},
			{"dob": "not a date"},
			{"dob": ""},
			{"dob": "2999-01-01"},
			{"dob": "1800-01-01"},
			{"mobile": "12345"},
			{"mobile": "5876543210"},
			{"mobile": "98765abcde"},
			{"mobile": None},
			{"email": "not-an-email"},
		]
		before = (patient_count(), frappe.db.count("Kiosk Token"))
		for override in bad:
			with self.subTest(override=override):
				self.assertRaises(frappe.ValidationError, kiosk.register_patient, **patient_args(**override))
		self.assertEqual((patient_count(), frappe.db.count("Kiosk Token")), before)

	def test_mobile_formats_are_normalised(self):
		for raw in ("+91 98765 43210", "09876543210", "919876543210", "98765-43210"):
			self.assertEqual(kiosk.normalise_mobile(raw), "9876543210")

	def test_same_mobile_and_dob_returns_the_existing_patient(self):
		args = patient_args()
		first = kiosk.register_patient(**args)
		count = patient_count()

		# +91 variant of the same number, different name spelling
		again = kiosk.register_patient(**{**args, "mobile": "+91" + args["mobile"], "first_name": "Aasha"})

		self.assertTrue(again["already_registered"])
		self.assertEqual(again["uhid"], first["uhid"])
		self.assertEqual(patient_count(), count)
		self.assertEqual(set(again), RESULT_KEYS)
		self.assertEqual(again["masked_mobile"], first["masked_mobile"])
		self.assertEqual(again["patient_name"], first["patient_name"])  # the stored name, not the new input

	def test_dedupe_ignores_the_duplicate_check_setting(self):
		args = patient_args()
		with as_admin():
			frappe.db.set_single_value("Healthcare Settings", "enable_patient_duplicate_check", 0)
		first = kiosk.register_patient(**args)
		self.assertEqual(kiosk.register_patient(**args)["uhid"], first["uhid"])

	def test_same_mobile_different_dob_is_a_new_patient(self):
		args = patient_args()
		first = kiosk.register_patient(**args)
		other = kiosk.register_patient(**{**args, "dob": "2001-01-01", "first_name": "Ravi"})
		self.assertNotEqual(first["uhid"], other["uhid"])
		self.assertFalse(other["already_registered"])

	def test_walk_in_tagging_comes_from_settings(self):
		result = kiosk.register_patient(**patient_args())
		source, camp = frappe.db.get_value("Patient", result["uhid"], ["patient_source", "medical_camp"])
		self.assertEqual(source, "Walk-in")
		self.assertFalse(camp)

	def test_camp_tagging_comes_from_settings(self):
		camp = make_camp()
		self.configure(patient_source="Medical Camp", medical_camp=camp.name)
		result = kiosk.register_patient(**patient_args())
		source, tagged = frappe.db.get_value("Patient", result["uhid"], ["patient_source", "medical_camp"])
		self.assertEqual((source, tagged), ("Medical Camp", camp.name))
		self.assertEqual(result["camp"], camp.camp_name)
		self.assertEqual(frappe.db.get_value("Kiosk Token", result["token"], "medical_camp"), camp.name)

	def test_client_cannot_set_server_fields(self):
		# Frappe passes an endpoint only the request fields its signature names
		request = {**patient_args(), "patient_source": "Referral", "medical_camp": "MC-X", "status": "Disabled", "abha_number": "1", "customer": "X"}
		accepted = frappe.get_newargs(kiosk.register_patient, request)
		self.assertEqual(set(accepted), {"first_name", "last_name", "sex", "dob", "mobile", "email"} & set(request))
		result = kiosk.register_patient(**accepted)
		patient = frappe.db.get_value("Patient", result["uhid"], ["patient_source", "medical_camp", "status"], as_dict=True)
		self.assertEqual(patient.patient_source, "Walk-in")
		self.assertFalse(patient.medical_camp)

	def test_inactive_camp_refuses_and_creates_nothing(self):
		camp = make_camp()
		self.configure(patient_source="Medical Camp", medical_camp=camp.name)
		frappe.db.set_value("Medical Camp", camp.name, "status", "Completed")
		before = patient_count()
		self.assertRaises(frappe.ValidationError, kiosk.register_patient, **patient_args())
		self.assertEqual(patient_count(), before)

	def test_tokens_count_up_within_a_day(self):
		first = kiosk.register_patient(**patient_args())
		second = kiosk.register_patient(**patient_args(first_name="Ravi"))
		self.assertEqual(second["token_number"], first["token_number"] + 1)
		self.assertTrue(first["token"].startswith("KT-" + nowdate().replace("-", "")))

	def test_registering_again_reuses_todays_token(self):
		args = patient_args()
		first = kiosk.register_patient(**args)
		again = kiosk.register_patient(**args)
		self.assertEqual(again["token"], first["token"])

	def test_registration_is_bound_to_the_session(self):
		result = kiosk.register_patient(**patient_args())
		kiosk_common.assert_bound(result["uhid"], require_created=True)
		again = kiosk.register_patient(**patient_args())
		with session_id("another-device"):
			self.assertRaises(frappe.PermissionError, kiosk_common.assert_bound, again["uhid"])

	def test_matched_patient_is_not_marked_created(self):
		args = patient_args()
		with session_id("device-1"):
			kiosk.register_patient(**args)
		with session_id("device-2"):
			matched = kiosk.register_patient(**args)
			kiosk_common.assert_bound(matched["uhid"])
			self.assertRaises(frappe.PermissionError, kiosk_common.assert_bound, matched["uhid"], True)

	def test_kiosk_user_cannot_read_patients(self):
		result = kiosk.register_patient(**patient_args())
		self.assertFalse(frappe.has_permission("Patient", "read"))
		self.assertFalse(frappe.has_permission("Patient", "read", result["uhid"]))
		self.assertFalse(frappe.has_permission("Patient", "write", result["uhid"]))
		self.assertTrue(frappe.has_permission("Patient", "create"))

	def test_denied_to_everyone_but_the_kiosk_user(self):
		for user in ("Guest", STAFF_USER):
			frappe.set_user(user)
			self.assertRaises(frappe.PermissionError, kiosk.register_patient, **patient_args())

	def test_disabled_kiosk_registers_nobody(self):
		self.configure(enabled=0)
		before = patient_count()
		self.assertRaises(frappe.ValidationError, kiosk.register_patient, **patient_args())
		self.assertEqual(patient_count(), before)

	def test_rate_limited(self):
		for i in range(10):
			kiosk.register_patient(**patient_args(first_name=f"Rate{i}"))
		with self.assertRaises(frappe.ValidationError):
			kiosk.register_patient(**patient_args())
		self.assertEqual(frappe.local.response.get("http_status_code"), 429)

	def test_get_config(self):
		config = kiosk.get_config()
		self.assertEqual(set(config), {"idle_timeout", "enable_abha", "enable_appointment", "camp", "camp_active"})
		self.assertEqual(config["idle_timeout"], 60)

	def test_end_session_forgets_patients(self):
		result = kiosk.register_patient(**patient_args())
		kiosk.end_session()
		self.assertRaises(frappe.PermissionError, kiosk_common.assert_bound, result["uhid"])


class TestKioskSlip(KioskTestCase):
	def test_slip_shows_the_five_values_and_nothing_private(self):
		camp = make_camp()
		self.configure(patient_source="Medical Camp", medical_camp=camp.name)
		args = patient_args()
		result = kiosk.register_patient(**args)

		html = kiosk.get_token_slip(result["token"])["html"]
		today = frappe.utils.formatdate(nowdate())

		for expected in (result["uhid"], result["patient_name"], str(result["token_number"]), camp.camp_name, today):
			self.assertIn(expected, html)
		for private in ("1990", "17-05", args["mobile"], args["mobile"][-4:], "ABHA", "abha"):
			self.assertNotIn(private, html)

	def test_slip_for_a_matched_patient(self):
		args = patient_args()
		with session_id("device-1"):
			first = kiosk.register_patient(**args)
		with session_id("device-2"):
			again = kiosk.register_patient(**args)
			self.assertIn(first["uhid"], kiosk.get_token_slip(again["token"])["html"])

	def test_other_session_cannot_get_the_slip(self):
		with session_id("device-1"):
			result = kiosk.register_patient(**patient_args())
		with session_id("device-2"):
			self.assertRaises(frappe.PermissionError, kiosk.get_token_slip, result["token"])

	def test_unknown_token_answers_like_a_foreign_one(self):
		for token in ("KT-19000101-0001", "", None, ["x"]):
			self.assertRaises(frappe.PermissionError, kiosk.get_token_slip, token)

	def test_slip_denied_to_other_users(self):
		result = kiosk.register_patient(**patient_args())
		frappe.set_user(STAFF_USER)
		self.assertRaises(frappe.PermissionError, kiosk.get_token_slip, result["token"])

	def test_user_is_restored_after_printing(self):
		result = kiosk.register_patient(**patient_args())
		kiosk.get_token_slip(result["token"])
		self.assertEqual(frappe.session.user, KIOSK_USER)


class TestKioskBooking(KioskTestCase):
	def at_camp(self, **settings):
		self.camp = make_camp()
		self.configure(patient_source="Medical Camp", medical_camp=self.camp.name, enable_appointment=1, **settings)
		return kiosk.register_patient(**patient_args())

	def test_booking_returns_only_the_allow_listed_keys(self):
		registered = self.at_camp()
		result = kiosk.book_appointment(registered["uhid"])

		self.assertEqual(set(result), {"appointment", "date", "time", "token_number"})
		self.assertEqual(result["date"], nowdate())
		self.assertEqual(result["token_number"], registered["token_number"])
		appointment = frappe.db.get_value(
			"Patient Appointment",
			result["appointment"],
			["patient", "appointment_for", "medical_camp", "appointment_type", "appointment_date"],
			as_dict=True,
		)
		self.assertEqual(appointment.patient, registered["uhid"])
		self.assertEqual(appointment.appointment_for, "Medical Camp")
		self.assertEqual(appointment.medical_camp, self.camp.name)
		self.assertEqual(appointment.appointment_type, CAMP_APPOINTMENT_TYPE)

	def test_booking_twice_returns_the_same_appointment(self):
		registered = self.at_camp()
		first = kiosk.book_appointment(registered["uhid"])
		count = frappe.db.count("Patient Appointment", {"patient": registered["uhid"]})
		self.assertEqual(kiosk.book_appointment(registered["uhid"])["appointment"], first["appointment"])
		self.assertEqual(frappe.db.count("Patient Appointment", {"patient": registered["uhid"]}), count)

	def test_registration_works_with_booking_off(self):
		self.configure()
		registered = kiosk.register_patient(**patient_args())
		self.assertTrue(registered["uhid"])
		self.assertRaises(frappe.ValidationError, kiosk.book_appointment, registered["uhid"])

	def test_booking_needs_a_camp_kiosk(self):
		self.configure(enable_appointment=1)  # Walk-in source: Kiosk Settings switches it back off
		registered = kiosk.register_patient(**patient_args())
		self.assertRaises(frappe.ValidationError, kiosk.book_appointment, registered["uhid"])

	def test_unbound_patient_is_refused(self):
		registered = self.at_camp()
		with session_id("device-2"):
			self.assertRaises(frappe.PermissionError, kiosk.book_appointment, registered["uhid"])
		for bad in ("NO-SUCH-PATIENT", "", None, ["x"]):
			self.assertRaises(frappe.PermissionError, kiosk.book_appointment, bad)

	def test_matched_patient_can_book(self):
		args = patient_args()
		self.at_camp()
		with session_id("device-1"):
			kiosk.register_patient(**args)
		with session_id("device-2"):
			matched = kiosk.register_patient(**args)
			self.assertTrue(kiosk.book_appointment(matched["uhid"])["appointment"])

	def test_closed_camp_refuses_booking(self):
		registered = self.at_camp()
		for change in ({"status": "Completed"}, {"status": "Cancelled"}, {"end_date": add_days(nowdate(), -1), "start_date": add_days(nowdate(), -2)}):
			frappe.db.set_value("Medical Camp", self.camp.name, {"status": "Ongoing", "start_date": nowdate(), "end_date": add_days(nowdate(), 1), **change})
			self.assertRaises(frappe.ValidationError, kiosk.book_appointment, registered["uhid"])
		self.assertEqual(frappe.db.count("Patient Appointment", {"patient": registered["uhid"]}), 0)

	def test_camp_without_appointment_type_is_unavailable(self):
		registered = self.at_camp()
		frappe.db.set_value("Medical Camp", self.camp.name, "appointment_type", None)
		self.assertRaises(frappe.ValidationError, kiosk.book_appointment, registered["uhid"])

	def test_booking_denied_to_other_users(self):
		registered = self.at_camp()
		for user in ("Guest", STAFF_USER):
			frappe.set_user(user)
			self.assertRaises(frappe.PermissionError, kiosk.book_appointment, registered["uhid"])


AADHAAR = "234567890124"  # shape only; the M1 call is mocked
ABHA_NUMBER = "91-1234-5678-9012"


def m1(module, name, **kwargs):
	"""Stand-in for an M1 function that records who it ran as."""
	ran_as = []

	def fake(*args, **kw):
		ran_as.append(frappe.session.user)
		return kwargs.get("return_value", {})

	mock = patch.object(module, name, side_effect=fake)
	mock.ran_as = ran_as
	return mock


class TestKioskAbha(KioskTestCase):
	def setUp(self):
		super().setUp()
		self.configure(enable_abha=1)
		self.uhid = kiosk.register_patient(**patient_args())["uhid"]

	def test_send_otp_returns_only_a_txn_id_and_binds_it(self):
		patcher = m1(enrol, "generate_aadhaar_otp", return_value={"message": "OTP sent", "txnId": "TXN-1"})
		with patcher as fn:
			result = kiosk_abha.send_aadhaar_otp(self.uhid, AADHAAR)
		self.assertEqual(result, {"txn_id": "TXN-1"})
		fn.assert_called_once_with(self.uhid, AADHAAR)
		kiosk_common.assert_txn_bound("TXN-1", self.uhid)

	def test_m1_runs_as_administrator_and_the_user_comes_back(self):
		patcher = m1(enrol, "generate_aadhaar_otp", return_value={"txnId": "TXN-1"})
		with patcher:
			kiosk_abha.send_aadhaar_otp(self.uhid, AADHAAR)
		self.assertEqual(patcher.ran_as, ["Administrator"])
		self.assertEqual(frappe.session.user, KIOSK_USER)

	def test_user_comes_back_when_m1_fails(self):
		with patch.object(enrol, "generate_aadhaar_otp", side_effect=RuntimeError("boom")):
			self.assertRaises(frappe.ValidationError, kiosk_abha.send_aadhaar_otp, self.uhid, AADHAAR)
		self.assertEqual(frappe.session.user, KIOSK_USER)

	def test_m1_error_text_is_not_passed_on(self):
		leak = "This ABHA number is already linked to a different patient record (HLC-PAT-9999)"
		with patch.object(enrol, "generate_aadhaar_otp", side_effect=frappe.ValidationError(leak)):
			with self.assertRaises(frappe.ValidationError) as ctx:
				kiosk_abha.send_aadhaar_otp(self.uhid, AADHAAR)
		self.assertNotIn("HLC-PAT-9999", str(ctx.exception))

	def test_every_step_refuses_a_patient_not_registered_in_this_session(self):
		with session_id("device-2"):
			calls = self.every_step(self.uhid, "TXN-X")
			for name, call in calls.items():
				with self.subTest(step=name), patch.object(enrol, "generate_aadhaar_otp") as a, patch.object(
					enrol, "enrol_by_aadhaar"
				) as b, patch.object(enrol, "set_abha_address") as c, patch.object(
					profile, "request_abha_address_otp"
				) as d:
					self.assertRaises(frappe.PermissionError, call)
					for fn in (a, b, c, d):
						fn.assert_not_called()

	def test_a_matched_patient_gets_no_abha_actions(self):
		args = patient_args()
		with session_id("device-1"):
			kiosk.register_patient(**args)
		with session_id("device-2"):
			matched = kiosk.register_patient(**args)
			self.assertTrue(matched["already_registered"])
			with patch.object(enrol, "generate_aadhaar_otp") as fn:
				self.assertRaises(frappe.PermissionError, kiosk_abha.send_aadhaar_otp, matched["uhid"], AADHAAR)
				fn.assert_not_called()

	def test_a_txn_this_session_did_not_start_is_refused(self):
		with patch.object(enrol, "enrol_by_aadhaar") as fn:
			self.assertRaises(frappe.PermissionError, kiosk_abha.verify_aadhaar_otp, self.uhid, "FOREIGN", "123456")
			fn.assert_not_called()

	def test_a_txn_cannot_be_used_for_another_patient(self):
		other = kiosk.register_patient(**patient_args())["uhid"]
		with patch.object(enrol, "generate_aadhaar_otp", return_value={"txnId": "TXN-A"}):
			kiosk_abha.send_aadhaar_otp(self.uhid, AADHAAR)
		with patch.object(enrol, "enrol_by_aadhaar") as fn:
			self.assertRaises(frappe.PermissionError, kiosk_abha.verify_aadhaar_otp, other, "TXN-A", "123456")
			fn.assert_not_called()

	def test_verify_returns_only_masked_values(self):
		with patch.object(enrol, "generate_aadhaar_otp", return_value={"txnId": "TXN-1"}):
			kiosk_abha.send_aadhaar_otp(self.uhid, AADHAAR)
		enrolled = {
			"message": "ABHA created successfully",
			"abha_number": ABHA_NUMBER,
			"abha_address": "asha.verma@sbx",
			"status": "PENDING",
			"mobile_linked": True,
			"txnId": "TXN-2",
			"tokens": {"token": "SECRET-X-TOKEN"},
		}
		with patch.object(enrol, "enrol_by_aadhaar", return_value=enrolled):
			result = kiosk_abha.verify_aadhaar_otp(self.uhid, "TXN-1", "123456", "9876543210")

		self.assertEqual(set(result), {"status", "mobile_linked", "masked_abha_number", "txn_id"})
		self.assertEqual(result["masked_abha_number"], "XX-XXXX-XXXX-9012")
		self.assertEqual(result["txn_id"], "TXN-2")
		kiosk_common.assert_txn_bound("TXN-2", self.uhid)
		self.assertNotIn("SECRET", str(result))
		self.assertNotIn("1234-5678", str(result))
		self.assertNotIn("asha.verma", str(result))

	def test_linking_strips_the_profile_m1_returns(self):
		with patch.object(profile, "request_abha_address_otp", return_value={"txnId": "TXN-L"}):
			self.assertEqual(kiosk_abha.send_link_otp(self.uhid, "asha.verma@sbx"), {"txn_id": "TXN-L"})
		full_profile = {
			"abha_number": "91123456789012",
			"abha_address": "asha.verma@sbx",
			"name": "Asha Verma",
			"mobile": "9876543210",
			"dob": "1990-05-17",
			"gender": "F",
			"x_token": "SECRET",
		}
		with patch.object(profile, "verify_abha_address_otp", return_value=full_profile):
			result = kiosk_abha.verify_link_otp(self.uhid, "TXN-L", "123456")

		self.assertEqual(set(result), {"status", "masked_abha_number", "masked_abha_address"})
		self.assertEqual(result["masked_abha_address"], "as********@sbx")
		for private in ("Asha Verma", "9876543210", "1990", "SECRET", "91123456789012"):
			self.assertNotIn(private, str(result))

	def test_mobile_create_flow(self):
		with patch.object(enrol, "generate_mobile_otp", return_value={"txnId": "TXN-M"}):
			self.assertEqual(kiosk_abha.send_mobile_otp_create(self.uhid, "9876543210"), {"txn_id": "TXN-M"})
		with patch.object(enrol, "verify_mobile_otp_enrol", return_value={"txnId": "TXN-M2", "message": "OTP verified"}):
			self.assertEqual(kiosk_abha.verify_mobile_otp_create(self.uhid, "TXN-M", "123456"), {"txn_id": "TXN-M2"})
		with patch.object(enrol, "get_abha_suggestions", return_value={"suggestions": ["asha.v@sbx", "<script>", 7, "bad address@sbx"]}):
			self.assertEqual(kiosk_abha.get_address_suggestions(self.uhid), {"suggestions": ["asha.v@sbx"]})
		with patch.object(enrol, "set_abha_address", return_value={"message": "ABHA address confirmed"}) as fn:
			result = kiosk_abha.choose_address(self.uhid, "asha.v@sbx")
		fn.assert_called_once_with(self.uhid, "asha.v@sbx")
		self.assertEqual(result, {"status": "saved", "masked_abha_address": "as****@sbx"})

	def test_aadhaar_mobile_verification_flow(self):
		with patch.object(enrol, "generate_aadhaar_otp", return_value={"txnId": "TXN-1"}):
			kiosk_abha.send_aadhaar_otp(self.uhid, AADHAAR)
		with patch.object(enrol, "send_mobile_otp", return_value={"txnId": "TXN-3"}):
			self.assertEqual(kiosk_abha.send_enrol_mobile_otp(self.uhid, "TXN-1", "9876543210"), {"txn_id": "TXN-3"})
		with patch.object(enrol, "verify_mobile_otp", return_value={"message": "Mobile verified"}):
			self.assertEqual(kiosk_abha.verify_enrol_mobile_otp(self.uhid, "TXN-3", "123456"), {"status": "verified", "txn_id": "TXN-3"})

	def test_bad_input_never_reaches_m1(self):
		with patch.object(enrol, "generate_aadhaar_otp") as send, patch.object(enrol, "enrol_by_aadhaar") as enrol_fn, patch.object(
			enrol, "set_abha_address"
		) as address:
			for aadhaar in ("12345", "abcdefghijkl", "", None):
				self.assertRaises(frappe.ValidationError, kiosk_abha.send_aadhaar_otp, self.uhid, aadhaar)
			kiosk_common.bind_txn("TXN-1", self.uhid)
			for otp in ("12345", "1234567", "abcdef", None):
				self.assertRaises(frappe.ValidationError, kiosk_abha.verify_aadhaar_otp, self.uhid, "TXN-1", otp)
			for address_value in ("no-domain", "x@evil.com", "a@sbx", "<b>@sbx", None):
				self.assertRaises(frappe.ValidationError, kiosk_abha.choose_address, self.uhid, address_value)
			for fn in (send, enrol_fn, address):
				fn.assert_not_called()

	def test_otp_sends_are_limited_per_patient(self):
		with patch.object(enrol, "generate_aadhaar_otp", return_value={"txnId": "T"}) as fn:
			for _ in range(3):
				kiosk_abha.send_aadhaar_otp(self.uhid, AADHAAR)
			with self.assertRaises(frappe.ValidationError):
				kiosk_abha.send_aadhaar_otp(self.uhid, AADHAAR)
			self.assertEqual(fn.call_count, 3)
		self.assertEqual(frappe.local.response.get("http_status_code"), 429)

	def test_a_stranger_cannot_use_up_a_patients_otp_allowance(self):
		with session_id("device-2"), patch.object(enrol, "generate_aadhaar_otp") as fn:
			for _ in range(5):
				self.assertRaises(frappe.PermissionError, kiosk_abha.send_aadhaar_otp, self.uhid, AADHAAR)
			fn.assert_not_called()
		with patch.object(enrol, "generate_aadhaar_otp", return_value={"txnId": "T"}):
			self.assertEqual(kiosk_abha.send_aadhaar_otp(self.uhid, AADHAAR), {"txn_id": "T"})

	def test_off_when_abha_is_disabled(self):
		self.configure(enable_abha=0)
		with patch.object(enrol, "generate_aadhaar_otp") as fn:
			self.assertRaises(frappe.ValidationError, kiosk_abha.send_aadhaar_otp, self.uhid, AADHAAR)
			fn.assert_not_called()

	def test_denied_to_everyone_but_the_kiosk_user(self):
		for user in ("Guest", STAFF_USER):
			frappe.set_user(user)
			self.assertRaises(frappe.PermissionError, kiosk_abha.send_aadhaar_otp, self.uhid, AADHAAR)

	def test_the_m1_guard_still_refuses_a_kiosk_user(self):
		# The unchanged guard: a create-only Kiosk user cannot drive M1 directly...
		self.assertRaises(frappe.PermissionError, AbhaClient._assert_patient_permission, None, self.uhid)
		# ...and it only passes inside the wrapper's elevation
		with kiosk_common.elevated():
			AbhaClient._assert_patient_permission(None, self.uhid)

	def every_step(self, uhid, txn):
		return {
			"send_aadhaar_otp": lambda: kiosk_abha.send_aadhaar_otp(uhid, AADHAAR),
			"verify_aadhaar_otp": lambda: kiosk_abha.verify_aadhaar_otp(uhid, txn, "123456"),
			"send_enrol_mobile_otp": lambda: kiosk_abha.send_enrol_mobile_otp(uhid, txn, "9876543210"),
			"verify_enrol_mobile_otp": lambda: kiosk_abha.verify_enrol_mobile_otp(uhid, txn, "123456"),
			"send_mobile_otp_create": lambda: kiosk_abha.send_mobile_otp_create(uhid, "9876543210"),
			"verify_mobile_otp_create": lambda: kiosk_abha.verify_mobile_otp_create(uhid, txn, "123456"),
			"get_address_suggestions": lambda: kiosk_abha.get_address_suggestions(uhid),
			"choose_address": lambda: kiosk_abha.choose_address(uhid, "asha.v@sbx"),
			"send_link_otp": lambda: kiosk_abha.send_link_otp(uhid, "asha.v@sbx"),
			"verify_link_otp": lambda: kiosk_abha.verify_link_otp(uhid, txn, "123456"),
		}


class TestKioskPage(KioskTestCase):
	def context(self):
		return kiosk_page.get_context(frappe._dict())

	def boot(self, context):
		return json.loads(context.boot_json.replace("\\u003c", "<").replace("\\u003e", ">").replace("\\u0026", "&"))

	def test_guest_is_sent_to_login_and_back(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.Redirect):
			self.context()
		self.assertEqual(frappe.local.flags.redirect_location, "/login?redirect-to=%2Fkiosk")

	def test_other_roles_are_refused(self):
		frappe.set_user(STAFF_USER)
		self.assertRaises(frappe.PermissionError, self.context)

	def test_kiosk_user_gets_what_the_page_needs_and_no_patient_data(self):
		self.configure(idle_timeout=45, enable_abha=1)
		kiosk.register_patient(**patient_args())  # a patient exists; none of it may reach the HTML
		context = self.context()

		self.assertEqual(context.no_cache, 1)
		self.assertIn("/assets/healthcare/kiosk/kiosk.js", context.js_url)
		self.assertIn("/assets/healthcare/kiosk/kiosk.css", context.css_url)
		boot = self.boot(context)
		self.assertEqual(
			set(boot),
			{"csrf_token", "enabled", "preview", "idle_timeout", "enable_abha", "enable_appointment", "camp", "camp_active"},
		)
		self.assertEqual(boot["idle_timeout"], 45)
		self.assertTrue(boot["enable_abha"])
		self.assertFalse(boot["preview"])
		self.assertTrue(boot["csrf_token"])

	def test_boot_json_cannot_close_its_script_tag(self):
		self.assertNotIn("</", kiosk_page._safe_json({"camp": "</script><script>alert(1)</script>"}))

	def test_system_manager_may_preview(self):
		make_user(STAFF_USER, ["System Manager"])
		frappe.set_user(STAFF_USER)
		boot = self.boot(self.context())
		self.assertTrue(boot["preview"])

	def test_page_reports_a_disabled_kiosk(self):
		self.configure(enabled=0)
		self.assertFalse(self.boot(self.context())["enabled"])

	def test_page_reports_a_closed_camp(self):
		camp = make_camp()
		self.configure(patient_source="Medical Camp", medical_camp=camp.name, enable_appointment=1)
		frappe.db.set_value("Medical Camp", camp.name, "status", "Completed")
		boot = self.boot(self.context())
		self.assertFalse(boot["camp_active"])
		self.assertFalse(boot["enable_appointment"])
		self.assertEqual(boot["camp"], camp.camp_name)
