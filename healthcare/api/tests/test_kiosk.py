# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# See license.txt

import contextlib
import json
import re
import types
from unittest.mock import patch

from redis.exceptions import LockError

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

from healthcare import setup as healthcare_setup
from healthcare.api import kiosk, kiosk_abha, kiosk_common
from healthcare.kiosk_setup import KIOSK_DOCTYPES, KIOSK_ROLE, ensure_kiosk_role_and_permissions
from healthcare.regional.india.abdm.api import enrol, profile
from healthcare.regional.india.abdm.utils.abha_client import AbhaClient
from healthcare.www import kiosk as kiosk_page

PERM_TYPES = (
	"read",
	"write",
	"create",
	"delete",
	"submit",
	"cancel",
	"amend",
	"report",
	"export",
	"import",
	"share",
	"print",
	"email",
	"select",
)


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
			{
				"doctype": "User",
				"email": email,
				"first_name": "KioskTest",
				"send_welcome_email": 0,
				"enabled": 1,
			}
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
			{
				"doctype": "Appointment Type",
				"appointment_type": CAMP_APPOINTMENT_TYPE,
				"allow_booking_for": "Medical Camp",
			}
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
		# The endpoints commit inside their locks; keep the test data rolled back, and let tests see the calls
		patcher = patch.object(frappe.db, "commit")
		self.commit = patcher.start()
		self.addCleanup(patcher.stop)

	def tearDown(self):
		frappe.set_user("Administrator")

	@contextlib.contextmanager
	def watch_locks(self):
		"""Record lock enter/exit and commits in order."""
		events = []
		real_lock = frappe.cache.lock

		@contextlib.contextmanager
		def spy(name, *args, **kwargs):
			with real_lock(name, *args, **kwargs):
				events.append("lock")
				yield
				events.append("unlock")

		self.commit.side_effect = lambda *a, **k: events.append("commit")
		with patch.object(frappe.cache, "lock", side_effect=spy):
			yield events
		self.commit.side_effect = None

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

	def test_duplicate_tokens_are_removed_before_the_unique_index_is_added(self):
		# A site that issued tokens before the index existed must still migrate. Dropping the index is DDL,
		# which commits, so these rows are real and the test removes them itself.
		patient = "KIOSK-DUP-TEST"
		index = "unique_kiosk_token_patient_day"
		frappe.db.sql(f"alter table `tabKiosk Token` drop index `{index}`")
		try:
			names = []
			for _ in range(2):
				doc = frappe.get_doc(
					{
						"doctype": "Kiosk Token",
						"patient": patient,
						"patient_name": "x",
						"token_date": nowdate(),
					}
				)
				doc.insert(ignore_permissions=True, ignore_links=True)
				names.append(doc.name)
			ensure_kiosk_role_and_permissions()  # must not raise on the duplicate
			self.assertEqual(
				frappe.get_all("Kiosk Token", filters={"patient": patient}, pluck="name"), [min(names)]
			)
			self.assertTrue(frappe.db.sql("show index from `tabKiosk Token` where Key_name = %s", index))
		finally:
			frappe.db.delete("Kiosk Token", {"patient": patient})
			frappe.db.commit()
			ensure_kiosk_role_and_permissions()  # leaves the index in place for the other tests

	def test_other_roles_keep_their_permissions(self):
		# Frappe ignores every standard DocPerm once a single Custom DocPerm exists for the doctype,
		# so a naive insert for Kiosk would strip Physician and friends of their access.
		for doctype in KIOSK_DOCTYPES:
			standard = {
				r.role
				for r in frappe.get_all(
					"DocPerm", filters={"parent": doctype, "permlevel": 0, "read": 1}, fields=["role"]
				)
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

	def test_rate_limit_raises_a_429(self):
		for _ in range(10):
			kiosk_common.throttle("kiosk_test")
		with self.assertRaises(frappe.TooManyRequestsError) as ctx:
			kiosk_common.throttle("kiosk_test")
		# Frappe answers with the exception's own status; a preset response code would be overwritten with 417
		self.assertEqual(ctx.exception.http_status_code, 429)

	def test_rate_limit_counter_always_has_an_expiry(self):
		kiosk_common.throttle("kiosk_ttl_test")
		key = frappe.cache.make_key(f"kiosk:rate:{frappe.session.user}:kiosk_ttl_test")
		self.assertGreater(frappe.cache.ttl(key), 0)

	def test_rate_limit_error_passes_through_the_endpoint_guard(self):
		@kiosk_common.kiosk_endpoint
		def slow():
			kiosk_common.throttle("kiosk_guard_test")

		for _ in range(10):
			slow()
		self.assertRaises(frappe.TooManyRequestsError, slow)

	def test_elevated_restores_user(self):
		with session_id("sid-A"):
			with kiosk_common.elevated():
				self.assertEqual(frappe.session.user, "Administrator")
			self.assertEqual(frappe.session.user, KIOSK_USER)
			# set_user() would leave the username here, and the session binding would be looked up under it
			self.assertEqual(frappe.session.sid, "sid-A")

	def test_elevated_restores_request_state(self):
		frappe.local.form_dict = frappe._dict(patient="PAT-1")
		frappe.local.session.data = frappe._dict(marker=1)
		with kiosk_common.elevated():
			pass
		self.assertEqual(frappe.local.form_dict.patient, "PAT-1")
		self.assertEqual(frappe.local.session.data.marker, 1)

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

	def test_session_binding_expiry_is_refreshed_on_use(self):
		with session_id("sid-A"):
			kiosk_common.bind_patient("PAT-1", created=True)
			kiosk_common.bind_txn("TXN-1", "PAT-1")
			pkey = frappe.cache.make_key(kiosk_common._patient_key("PAT-1"))
			tkey = frappe.cache.make_key(kiosk_common._txn_key("TXN-1"))
			frappe.cache.expire(pkey, 5)
			frappe.cache.expire(tkey, 5)
			kiosk_common.assert_bound("PAT-1")
			kiosk_common.assert_txn_bound("TXN-1", "PAT-1")
			self.assertGreater(frappe.cache.ttl(pkey), 30)
			self.assertGreater(frappe.cache.ttl(tkey), 30)

	def test_binding_one_patient_does_not_drop_another(self):
		with session_id("sid-A"):
			kiosk_common.bind_patient("PAT-1", created=True)
			kiosk_common.bind_txn("TXN-1", "PAT-1")
			kiosk_common.bind_patient("PAT-2", created=False)
			kiosk_common.assert_bound("PAT-1", require_created=True)
			kiosk_common.assert_txn_bound("TXN-1", "PAT-1")

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
# A returning patient is greeted by first name only: no UHID and no full name reach the screen
MATCHED_KEYS = {"first_name", "already_registered", "masked_mobile", "token_number", "token", "camp"}


def uhid_of(result):
	"""The patient behind a result. A matched result carries no UHID, so ask the token."""
	return frappe.db.get_value("Kiosk Token", result["token"], "patient")


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
				frappe.cache.delete_keys(
					"kiosk:rate:"
				)  # 15 bad submissions would otherwise hit the 10/min cap
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
		self.assertEqual(uhid_of(again), first["uhid"])
		self.assertEqual(patient_count(), count)
		self.assertEqual(set(again), MATCHED_KEYS)
		self.assertEqual(again["masked_mobile"], first["masked_mobile"])
		# the stored first name, not the new input, and neither the UHID nor the full name
		self.assertEqual(again["first_name"], frappe.db.get_value("Patient", first["uhid"], "first_name"))
		self.assertNotIn(first["uhid"], json.dumps(again))
		self.assertNotIn(first["patient_name"], json.dumps(again))

	def test_dedupe_ignores_the_duplicate_check_setting(self):
		args = patient_args()
		with as_admin():
			frappe.db.set_single_value("Healthcare Settings", "enable_patient_duplicate_check", 0)
		first = kiosk.register_patient(**args)
		self.assertEqual(uhid_of(kiosk.register_patient(**args)), first["uhid"])

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
		request = {
			**patient_args(),
			"patient_source": "Referral",
			"medical_camp": "MC-X",
			"status": "Disabled",
			"abha_number": "1",
			"customer": "X",
		}
		accepted = frappe.get_newargs(kiosk.register_patient, request)
		self.assertEqual(
			set(accepted), {"first_name", "last_name", "sex", "dob", "mobile", "email"} & set(request)
		)
		result = kiosk.register_patient(**accepted)
		patient = frappe.db.get_value(
			"Patient", result["uhid"], ["patient_source", "medical_camp", "status"], as_dict=True
		)
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
			self.assertRaises(frappe.PermissionError, kiosk_common.assert_bound, uhid_of(again))

	def test_matched_patient_is_not_marked_created(self):
		args = patient_args()
		with session_id("device-1"):
			kiosk.register_patient(**args)
		with session_id("device-2"):
			matched = kiosk.register_patient(**args)
			kiosk_common.assert_bound(uhid_of(matched))
			self.assertRaises(frappe.PermissionError, kiosk_common.assert_bound, uhid_of(matched), True)

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

	def test_register_commits_inside_its_lock(self):
		# The next holder of the lock must see this holder's rows: commit before unlock, and one on entry
		# to end the old snapshot so a row committed a moment ago is not hidden by REPEATABLE READ.
		with self.watch_locks() as events:
			kiosk.register_patient(**patient_args())
		self.assertEqual(events, ["lock", "commit", "commit", "unlock"])

	def test_one_token_per_patient_per_day_is_enforced_by_the_database(self):
		result = kiosk.register_patient(**patient_args())
		duplicate = frappe.get_doc(
			{
				"doctype": "Kiosk Token",
				"patient": result["uhid"],
				"patient_name": "x",
				"token_date": nowdate(),
			}
		)
		with as_admin():
			self.assertRaises(frappe.UniqueValidationError, duplicate.insert, ignore_permissions=True)
		frappe.db.rollback()

	def test_a_lost_race_for_the_token_returns_the_winners_token(self):
		first = kiosk.register_patient(**patient_args())
		patient = frappe.get_doc("Patient", first["uhid"])
		# the existence check (the first read) misses it, as if the other request had not committed yet
		real_get_value = frappe.db.get_value
		calls = []

		def stale_then_real(doctype, *args, **kwargs):
			if doctype == "Kiosk Token" and not calls:
				calls.append(1)
				return None
			return real_get_value(doctype, *args, **kwargs)

		# In production the winner has committed, so the loser's rollback only drops its own pending work;
		# here nothing is committed, so keep the rollback from wiping the winner too.
		with (
			patch.object(frappe.db, "get_value", side_effect=stale_then_real),
			patch.object(frappe.db, "rollback"),
		):
			token = kiosk._issue_token(patient, None)
		self.assertEqual(token.name, first["token"])
		self.assertEqual(
			frappe.db.count("Kiosk Token", {"patient": first["uhid"], "token_date": nowdate()}), 1
		)

	def test_a_lost_token_race_leaves_no_unique_message_for_the_screen(self):
		first = kiosk.register_patient(**patient_args())
		patient = frappe.get_doc("Patient", first["uhid"])
		real_get_value = frappe.db.get_value
		calls = []

		def stale_then_real(doctype, *args, **kwargs):
			if doctype == "Kiosk Token" and not calls:
				calls.append(1)
				return None
			return real_get_value(doctype, *args, **kwargs)

		frappe.clear_messages()
		with (
			patch.object(frappe.db, "get_value", side_effect=stale_then_real),
			patch.object(frappe.db, "rollback"),
		):
			kiosk._issue_token(patient, None)
		self.assertFalse([m for m in frappe.local.message_log if "unique" in str(m).lower()])

	def test_a_token_unique_violation_with_no_winner_is_answered_generically(self):
		real_insert = frappe.model.document.Document.insert

		def insert(doc, *args, **kwargs):
			if doc.doctype == "Kiosk Token":
				raise frappe.UniqueValidationError("Kiosk Token KT-20990101-0001 must be unique")
			return real_insert(doc, *args, **kwargs)

		with (
			patch.object(frappe.model.document.Document, "insert", insert),
			patch.object(frappe.db, "rollback"),
			patch.object(frappe, "log_error"),
		):
			with self.assertRaises(frappe.ValidationError) as ctx:
				kiosk.register_patient(**patient_args())
		self.assertIn("ask for help", str(ctx.exception))
		self.assertNotIn("unique", str(ctx.exception))
		self.assertNotIn("KT-", str(ctx.exception))

	def test_a_lost_token_race_ends_in_the_generic_refusal_and_binds_nothing(self):
		# The loser's rollback takes its new patient with it, so no winner is found for it: refuse, bind nobody.
		def lose_the_race(doc, *args, **kwargs):
			if doc.doctype == "Kiosk Token":
				raise frappe.UniqueValidationError("Kiosk Token KT-20990101-0001 must be unique")
			return real_insert(doc, *args, **kwargs)

		real_insert = frappe.model.document.Document.insert
		with (
			patch.object(frappe.model.document.Document, "insert", lose_the_race),
			patch.object(frappe.db, "rollback"),
			patch.object(kiosk_common, "bind_patient") as bind,
		):
			with self.assertRaises(frappe.ValidationError) as ctx:
				kiosk.register_patient(**patient_args(mobile="9123456781"))
		self.assertIn("ask for help", str(ctx.exception))
		bind.assert_not_called()

	def test_a_logging_failure_falls_back_to_the_file_logger_with_the_class_only(self):
		saved = frappe.local.form_dict
		frappe.local.form_dict = frappe._dict(mobile="9876543210")
		logger = types.SimpleNamespace(error=lambda *a, **k: calls.append(a))
		calls = []
		try:
			with (
				patch.object(frappe, "log_error", side_effect=RuntimeError("Asha 9876543210")),
				patch.object(frappe, "logger", return_value=logger),
			):
				kiosk_common.log_deferred("Kiosk lock lost", "LockError")  # must not raise
			self.assertEqual(frappe.local.form_dict.get("mobile"), "9876543210")  # restored
		finally:
			frappe.local.form_dict = saved
		self.assertEqual(len(calls), 1)
		self.assertNotIn("9876543210", str(calls))
		self.assertNotIn("Asha", str(calls))
		self.assertIn("RuntimeError", str(calls))

	def test_a_token_insert_failure_is_answered_generically(self):
		real_insert = frappe.model.document.Document.insert

		def insert(doc, *args, **kwargs):
			if doc.doctype == "Kiosk Token":
				raise frappe.ValidationError("HLC-PAT-9999 Asha Verma")
			return real_insert(doc, *args, **kwargs)

		with patch.object(frappe.model.document.Document, "insert", insert):
			with self.assertRaises(frappe.ValidationError) as ctx:
				kiosk.register_patient(**patient_args())
		self.assertNotIn("Asha", str(ctx.exception))
		self.assertNotIn("HLC-PAT-9999", str(ctx.exception))

	def test_the_returning_patients_binding_is_written_inside_the_lock(self):
		with self.watch_locks() as events:
			with patch.object(
				kiosk_common, "bind_patient", side_effect=lambda *a, **k: events.append("bind")
			):
				kiosk.register_patient(**patient_args())
		self.assertEqual(events, ["lock", "commit", "bind", "commit", "unlock"])

	def test_a_busy_lock_says_so_and_logs_only_the_class(self):
		with (
			patch.object(frappe.cache, "lock", side_effect=LockError("Asha 9876543210")),
			patch.object(frappe, "log_error") as log,
		):
			with self.assertRaises(frappe.ValidationError) as ctx:
				kiosk.register_patient(**patient_args())
		self.assertIn("busy", str(ctx.exception))
		self.assertNotIn("9876543210", str(log.call_args_list))
		self.assertTrue(log.call_args_list)

	def test_a_lock_lost_at_release_does_not_undo_a_finished_registration(self):
		# The patient and token are committed before the lock is released, so a release failure is only logged.
		@contextlib.contextmanager
		def lock_lost_at_release(*args, **kwargs):
			yield
			raise LockError("not owned Asha 9876543210")

		with (
			patch.object(frappe.cache, "lock", side_effect=lock_lost_at_release),
			patch.object(frappe, "log_error") as log,
		):
			result = kiosk.register_patient(**patient_args())
		self.assertTrue(result["token"])
		self.assertTrue(log.call_args_list)
		self.assertTrue(log.call_args_list[0].kwargs.get("defer_insert"))
		self.assertNotIn("9876543210", str(log.call_args_list))
		self.assertNotIn("Asha", str(log.call_args_list))

	def test_a_lock_lost_at_release_does_not_mask_the_real_failure(self):
		@contextlib.contextmanager
		def lock_lost_at_release(*args, **kwargs):
			try:
				yield
			finally:
				raise LockError("not owned")

		with (
			patch.object(frappe.cache, "lock", side_effect=lock_lost_at_release),
			patch.object(frappe, "log_error"),
		):
			# inside the lock: _find_existing_patient is its first step
			with patch.object(kiosk, "_find_existing_patient", side_effect=RuntimeError("Asha")):
				with self.assertRaises(frappe.ValidationError) as ctx:
					kiosk.register_patient(**patient_args())
		self.assertIn("ask for help", str(ctx.exception))
		self.assertNotIn("busy", str(ctx.exception))

	def test_a_refusal_log_never_carries_the_request_arguments(self):
		# frappe.log_error stores sanitized_dict(form_dict) in Error Log.metadata, and that only masks keys such
		# as password/token/key, so dob, mobile, aadhaar and otp would be stored in clear text.
		sensitive = {
			"first_name": "Asha",
			"dob": "1990-02-03",
			"mobile": "9876543210",
			"aadhaar": "123412341234",
			"otp": "654321",
		}
		queued = []
		saved_form_dict = frappe.local.form_dict
		had_request = hasattr(frappe.local, "request")
		saved_request = getattr(frappe.local, "request", None)
		frappe.local.request = frappe._dict(method="POST", path="/api/method/kiosk", referrer="")
		frappe.local.form_dict = frappe._dict(sensitive)
		try:
			with patch("frappe.deferred_insert.deferred_insert", side_effect=lambda **kw: queued.append(kw)):
				with self.assertRaises(frappe.ValidationError):
					kiosk_common.refuse(
						"Kiosk request failed", "register_patient: RuntimeError", "Generic answer"
					)
			restored = dict(frappe.local.form_dict)
		finally:
			frappe.local.form_dict = saved_form_dict
			if had_request:
				frappe.local.request = saved_request
			else:
				del frappe.local.request
		self.assertEqual(len(queued), 1)
		record = str(queued[0])
		self.assertIn("http_request", record)  # the metadata was gathered, just without the arguments
		for value in sensitive.values():
			self.assertNotIn(value, record)
		self.assertEqual(restored, sensitive)  # the request's own form_dict is put back

	def test_a_logging_failure_does_not_replace_the_generic_answer(self):
		with patch.object(frappe, "log_error", side_effect=ConnectionError("redis down")):
			with self.assertRaises(frappe.ValidationError) as ctx:
				kiosk_common.refuse("Kiosk request failed", "x", "Generic answer")
		self.assertEqual(str(ctx.exception), "Generic answer")

	def test_every_refusal_is_logged_deferred_so_the_rollback_keeps_it(self):
		# The request ends in an exception, so Frappe rolls back; a non-deferred Error Log row would go too.
		with patch.object(frappe, "log_error") as log:
			with patch.object(
				frappe.model.document.Document, "insert", side_effect=frappe.ValidationError("x")
			):
				self.assertRaises(frappe.ValidationError, kiosk.register_patient, **patient_args())
			with patch.object(kiosk, "_validate_demographics", side_effect=RuntimeError("Asha")):
				self.assertRaises(
					frappe.ValidationError, kiosk.register_patient, **patient_args(first_name="Other")
				)
		self.assertEqual(len(log.call_args_list), 2)
		for call in log.call_args_list:
			self.assertTrue(call.kwargs.get("defer_insert"), call)

	def test_a_missing_create_permission_is_answered_generically(self):
		with (
			patch.object(frappe, "has_permission", return_value=False),
			patch.object(frappe, "log_error") as log,
		):
			with self.assertRaises(frappe.ValidationError) as ctx:
				kiosk.register_patient(**patient_args())
		self.assertIn("ask for help", str(ctx.exception))
		self.assertTrue(log.call_args_list[0].kwargs.get("defer_insert"))

	def test_a_returning_patient_is_greeted_by_first_name_only(self):
		args = patient_args()
		with session_id("device-1"):
			first = kiosk.register_patient(**args)
		with session_id("device-2"):
			again = kiosk.register_patient(**args)
			self.assertNotIn("uhid", again)
			self.assertNotIn("patient_name", again)
			# the slip, fetched with the token, is where the UHID and full name appear
			html = kiosk.get_token_slip(again["token"])["html"]
			self.assertIn(first["uhid"], html)
			self.assertIn(first["patient_name"], html)

	def test_endpoints_accept_post_only(self):
		for fn in (
			kiosk.register_patient,
			kiosk.get_token_slip,
			kiosk.book_appointment,
			kiosk.get_config,
			kiosk.end_session,
		):
			self.assertEqual(frappe.allowed_http_methods_for_whitelisted_func[fn], ["POST"], fn.__name__)
		for fn in (kiosk_abha.send_aadhaar_otp, kiosk_abha.verify_link_otp, kiosk_abha.choose_address):
			self.assertEqual(frappe.allowed_http_methods_for_whitelisted_func[fn], ["POST"], fn.__name__)

	def test_refusals_are_logged_without_patient_data(self):
		with patch.object(frappe, "log_error") as log:
			with patch.object(
				frappe.model.document.Document,
				"insert",
				side_effect=frappe.ValidationError("Asha Verma 9876543210"),
			):
				self.assertRaises(frappe.ValidationError, kiosk.register_patient, **patient_args())
		logged = " ".join(str(c) for c in log.call_args_list)
		self.assertNotIn("Asha", logged)
		self.assertNotIn("9876543210", logged)

	def test_rate_limited(self):
		for i in range(10):
			kiosk.register_patient(**patient_args(first_name=f"Rate{i}"))
		with self.assertRaises(frappe.TooManyRequestsError):
			kiosk.register_patient(**patient_args())

	def test_get_config(self):
		config = kiosk.get_config()
		self.assertEqual(
			set(config), {"idle_timeout", "enable_abha", "enable_appointment", "camp", "camp_active"}
		)
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

		for expected in (
			result["uhid"],
			result["patient_name"],
			str(result["token_number"]),
			camp.camp_name,
			today,
		):
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


class TestKioskSlipFailure(KioskTestCase):
	def test_a_print_failure_is_answered_generically(self):
		result = kiosk.register_patient(**patient_args())
		with patch.object(
			frappe, "get_print", side_effect=frappe.PermissionError("Not permitted for HLC-PAT-9999")
		):
			with self.assertRaises(frappe.ValidationError) as ctx:
				kiosk.get_token_slip(result["token"])
		self.assertNotIn("HLC-PAT-9999", str(ctx.exception))
		self.assertIn("slip", str(ctx.exception))


class TestKioskBooking(KioskTestCase):
	def at_camp(self, **settings):
		self.camp = make_camp()
		self.configure(
			patient_source="Medical Camp", medical_camp=self.camp.name, enable_appointment=1, **settings
		)
		return kiosk.register_patient(**patient_args())

	def test_booking_returns_only_the_allow_listed_keys(self):
		registered = self.at_camp()
		result = kiosk.book_appointment(registered["token"])

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
		first = kiosk.book_appointment(registered["token"])
		count = frappe.db.count("Patient Appointment", {"patient": registered["uhid"]})
		self.assertEqual(kiosk.book_appointment(registered["token"])["appointment"], first["appointment"])
		self.assertEqual(frappe.db.count("Patient Appointment", {"patient": registered["uhid"]}), count)

	def test_registration_works_with_booking_off(self):
		self.configure()
		registered = kiosk.register_patient(**patient_args())
		self.assertTrue(registered["uhid"])
		self.assertRaises(frappe.ValidationError, kiosk.book_appointment, registered["token"])

	def test_booking_needs_a_camp_kiosk(self):
		self.configure(enable_appointment=1)  # Walk-in source: Kiosk Settings switches it back off
		registered = kiosk.register_patient(**patient_args())
		self.assertRaises(frappe.ValidationError, kiosk.book_appointment, registered["token"])

	def test_unbound_patient_is_refused(self):
		registered = self.at_camp()
		with session_id("device-2"):
			self.assertRaises(frappe.PermissionError, kiosk.book_appointment, registered["token"])
		for bad in ("NO-SUCH-PATIENT", "", None, ["x"]):
			self.assertRaises(frappe.PermissionError, kiosk.book_appointment, bad)

	def test_the_lock_names_do_not_carry_the_patients_dob_or_mobile(self):
		names = []
		real_lock = frappe.cache.lock

		def spy(name, *args, **kwargs):
			names.append(name)
			return real_lock(name, *args, **kwargs)

		with patch.object(frappe.cache, "lock", side_effect=spy):
			self.camp = make_camp()
			self.configure(patient_source="Medical Camp", medical_camp=self.camp.name, enable_appointment=1)
			registered = kiosk.register_patient(**patient_args(mobile="9123456789", dob="1991-04-05"))
			kiosk.book_appointment(registered["token"])
		self.assertEqual(len(names), 2)
		for name in names:
			text = name.decode() if isinstance(name, bytes) else str(name)
			self.assertNotIn("9123456789", text)
			self.assertNotIn("1991-04-05", text)
			self.assertNotIn(registered["uhid"], text)

	def test_matched_patient_can_book(self):
		args = patient_args()
		self.at_camp()
		with session_id("device-1"):
			kiosk.register_patient(**args)
		with session_id("device-2"):
			matched = kiosk.register_patient(**args)
			self.assertTrue(kiosk.book_appointment(matched["token"])["appointment"])

	def test_closed_camp_refuses_booking(self):
		registered = self.at_camp()
		for change in (
			{"status": "Completed"},
			{"status": "Cancelled"},
			{"end_date": add_days(nowdate(), -1), "start_date": add_days(nowdate(), -2)},
		):
			frappe.db.set_value(
				"Medical Camp",
				self.camp.name,
				{"status": "Ongoing", "start_date": nowdate(), "end_date": add_days(nowdate(), 1), **change},
			)
			self.assertRaises(frappe.ValidationError, kiosk.book_appointment, registered["token"])
		self.assertEqual(frappe.db.count("Patient Appointment", {"patient": registered["uhid"]}), 0)

	def test_camp_without_appointment_type_is_unavailable(self):
		registered = self.at_camp()
		frappe.db.set_value("Medical Camp", self.camp.name, "appointment_type", None)
		self.assertRaises(frappe.ValidationError, kiosk.book_appointment, registered["token"])

	def test_booking_commits_inside_its_lock(self):
		registered = self.at_camp()
		with self.watch_locks() as events:
			kiosk.book_appointment(registered["token"])
		self.assertEqual(events, ["lock", "commit", "commit", "unlock"])

	def test_booking_denied_to_other_users(self):
		registered = self.at_camp()
		for user in ("Guest", STAFF_USER):
			frappe.set_user(user)
			self.assertRaises(frappe.PermissionError, kiosk.book_appointment, registered["token"])


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

	def test_the_binding_survives_the_elevation(self):
		# The real flow is send OTP, then verify in a later request: both must see the same device's binding.
		with session_id("sid-A"):
			uhid = kiosk.register_patient(**patient_args(first_name="Sidtest"))["uhid"]
			with m1(enrol, "generate_aadhaar_otp", return_value={"txnId": "TXN-S"}):
				kiosk_abha.send_aadhaar_otp(uhid, AADHAAR)
			self.assertEqual(frappe.session.sid, "sid-A")
			kiosk_common.assert_txn_bound("TXN-S", uhid)
			with session_id("sid-B"):
				self.assertRaises(frappe.PermissionError, kiosk_common.assert_txn_bound, "TXN-S", uhid)

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

	def test_m1_permission_errors_are_not_passed_on_or_logged(self):
		leak = "No permission for Patient HLC-PAT-9999 (ABHA 91-1234-5678-9012)"
		with (
			patch.object(enrol, "generate_aadhaar_otp", side_effect=frappe.PermissionError(leak)),
			patch.object(frappe, "log_error") as log,
		):
			with self.assertRaises(frappe.ValidationError) as ctx:
				kiosk_abha.send_aadhaar_otp(self.uhid, AADHAAR)
		self.assertNotIn("HLC-PAT-9999", str(ctx.exception))
		self.assertNotIn("HLC-PAT-9999", str(log.call_args_list))
		self.assertNotIn("91-1234", str(log.call_args_list))

	def test_every_step_refuses_a_patient_not_registered_in_this_session(self):
		with session_id("device-2"):
			calls = self.every_step(self.uhid, "TXN-X")
			for name, call in calls.items():
				with (
					self.subTest(step=name),
					patch.object(enrol, "generate_aadhaar_otp") as a,
					patch.object(enrol, "enrol_by_aadhaar") as b,
					patch.object(enrol, "set_abha_address") as c,
					patch.object(profile, "request_abha_address_otp") as d,
				):
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
				self.assertRaises(
					frappe.PermissionError, kiosk_abha.send_aadhaar_otp, uhid_of(matched), AADHAAR
				)
				fn.assert_not_called()

	def test_a_txn_this_session_did_not_start_is_refused(self):
		with patch.object(enrol, "enrol_by_aadhaar") as fn:
			self.assertRaises(
				frappe.PermissionError, kiosk_abha.verify_aadhaar_otp, self.uhid, "FOREIGN", "123456"
			)
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
		with patch.object(
			enrol, "verify_mobile_otp_enrol", return_value={"txnId": "TXN-M2", "message": "OTP verified"}
		):
			self.assertEqual(
				kiosk_abha.verify_mobile_otp_create(self.uhid, "TXN-M", "123456"), {"txn_id": "TXN-M2"}
			)
		with patch.object(
			enrol,
			"get_abha_suggestions",
			return_value={"suggestions": ["asha.v@sbx", "<script>", 7, "bad address@sbx"]},
		):
			self.assertEqual(kiosk_abha.get_address_suggestions(self.uhid), {"suggestions": ["asha.v@sbx"]})
		with patch.object(
			enrol, "set_abha_address", return_value={"message": "ABHA address confirmed"}
		) as fn:
			result = kiosk_abha.choose_address(self.uhid, "asha.v@sbx")
		fn.assert_called_once_with(self.uhid, "asha.v@sbx")
		self.assertEqual(result, {"status": "saved", "masked_abha_address": "as****@sbx"})

	def test_aadhaar_mobile_verification_flow(self):
		with patch.object(enrol, "generate_aadhaar_otp", return_value={"txnId": "TXN-1"}):
			kiosk_abha.send_aadhaar_otp(self.uhid, AADHAAR)
		with patch.object(enrol, "send_mobile_otp", return_value={"txnId": "TXN-3"}):
			self.assertEqual(
				kiosk_abha.send_enrol_mobile_otp(self.uhid, "TXN-1", "9876543210"), {"txn_id": "TXN-3"}
			)
		with patch.object(enrol, "verify_mobile_otp", return_value={"message": "Mobile verified"}):
			self.assertEqual(
				kiosk_abha.verify_enrol_mobile_otp(self.uhid, "TXN-3", "123456"),
				{"status": "verified", "txn_id": "TXN-3"},
			)

	def test_bad_input_never_reaches_m1(self):
		with (
			patch.object(enrol, "generate_aadhaar_otp") as send,
			patch.object(enrol, "enrol_by_aadhaar") as enrol_fn,
			patch.object(enrol, "set_abha_address") as address,
		):
			for aadhaar in ("12345", "abcdefghijkl", "", None):
				self.assertRaises(frappe.ValidationError, kiosk_abha.send_aadhaar_otp, self.uhid, aadhaar)
			kiosk_common.bind_txn("TXN-1", self.uhid)
			for otp in ("12345", "1234567", "abcdef", None):
				self.assertRaises(
					frappe.ValidationError, kiosk_abha.verify_aadhaar_otp, self.uhid, "TXN-1", otp
				)
			for address_value in ("no-domain", "x@evil.com", "a@sbx", "<b>@sbx", None):
				self.assertRaises(frappe.ValidationError, kiosk_abha.choose_address, self.uhid, address_value)
			for fn in (send, enrol_fn, address):
				fn.assert_not_called()

	def test_otp_sends_are_limited_per_patient(self):
		with patch.object(enrol, "generate_aadhaar_otp", return_value={"txnId": "T"}) as fn:
			for _ in range(3):
				kiosk_abha.send_aadhaar_otp(self.uhid, AADHAAR)
			with self.assertRaises(frappe.TooManyRequestsError):
				kiosk_abha.send_aadhaar_otp(self.uhid, AADHAAR)
			self.assertEqual(fn.call_count, 3)

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
		return json.loads(
			context.boot_json.replace("\\u003c", "<").replace("\\u003e", ">").replace("\\u0026", "&")
		)

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
			{
				"csrf_token",
				"enabled",
				"preview",
				"idle_timeout",
				"enable_abha",
				"enable_appointment",
				"camp",
				"camp_active",
			},
		)
		self.assertEqual(boot["idle_timeout"], 45)
		self.assertTrue(boot["enable_abha"])
		self.assertFalse(boot["preview"])
		self.assertTrue(boot["csrf_token"])

	def test_template_renders_with_the_boot_json(self):
		context = self.context()
		with open(frappe.get_app_path("healthcare", "www", "kiosk.html")) as f:
			html = frappe.render_template(f.read(), context)
		self.assertIn('<script type="application/json" id="kiosk-boot">', html)
		self.assertIn(context.boot_json, html)
		self.assertIn(context.js_url, html)
		self.assertIn("<title>Patient Registration</title>", html)

	def test_boot_json_cannot_close_its_script_tag(self):
		self.assertNotIn("</", kiosk_page._safe_json({"camp": "</script><script>alert(1)</script>"}))

	def test_system_manager_may_preview(self):
		make_user(STAFF_USER, ["System Manager"])
		frappe.set_user(STAFF_USER)
		boot = self.boot(self.context())
		self.assertTrue(boot["preview"])

	def test_page_is_kept_out_of_the_sitemap(self):
		self.assertEqual(kiosk_page.no_sitemap, 1)
		self.assertEqual(kiosk_page.no_cache, 1)

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


class TestKioskAssets(IntegrationTestCase):
	"""Static guards on the hand-written UI: it may only talk to the kiosk API and must not keep or inject patient data."""

	def source(self, filename):
		with open(frappe.get_app_path("healthcare", "public", "kiosk", filename)) as f:
			return f.read()

	def test_js_only_calls_the_kiosk_api(self):
		js = self.source("kiosk.js")
		for base in re.findall(r'"(/api/method/[^"]*)"', js):
			self.assertRegex(base, r"^/api/method/healthcare\.api\.kiosk(_abha)?\.$")
		self.assertNotIn("http://", js)
		self.assertNotIn("https://", js)

	def test_a_refused_step_tells_the_patient_to_start_again_not_that_the_kiosk_is_signed_out(self):
		js = self.source("kiosk.js")
		self.assertIn(
			"Your session has ended. Please start again, or ask for help at the desk if this keeps happening.",
			js,
		)
		# Whitespace-tolerant: prettier may wrap the return onto its own line
		self.assertRegex(js, r'status === 401\)\s*return "This kiosk is not signed in')

	def test_js_keeps_nothing_and_injects_no_html(self):
		js = self.source("kiosk.js")
		for forbidden in (
			"localStorage",
			"sessionStorage",
			"document.cookie",
			"innerHTML",
			"outerHTML",
			"insertAdjacentHTML",
			"eval(",
			"location.search",
			"location.hash",
		):
			self.assertNotIn(forbidden, js)

	def test_page_loads_only_its_own_static_files(self):
		with open(frappe.get_app_path("healthcare", "www", "kiosk.html")) as f:
			html = f.read()
		self.assertEqual(len(re.findall(r"<script\b", html)), 2)  # boot JSON + kiosk.js
		self.assertNotRegex(html, r"https?://")


class TestKioskSetupHook(IntegrationTestCase):
	def test_kiosk_setup_runs_even_when_healthcare_is_already_set_up(self):
		with (
			patch("healthcare.kiosk_setup.ensure_kiosk_role_and_permissions") as ensure,
			patch.object(frappe.db, "exists", return_value=True),
		):  # the "Cardiology exists" early return
			healthcare_setup.setup_healthcare()
		ensure.assert_called_once()
