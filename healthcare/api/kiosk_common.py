"""Shared guards for the registration kiosk API (healthcare.api.kiosk and healthcare.api.kiosk_abha).

The kiosk is one shared, unattended device signed in as a user holding only the Kiosk role. Everything here
is server-authoritative: the client may only say what a patient typed, never who they are or what to tag.
"""

import contextlib
import functools

import frappe
from frappe import _
from frappe.utils import getdate, nowdate

from healthcare.kiosk_setup import KIOSK_ROLE
from healthcare.regional.india.abdm.utils.http_guards import require_post

# Slack on top of the idle timeout so a patient finishing at the last second keeps their session.
SESSION_GRACE_SECONDS = 30
API_CALL_LIMIT = 10  # per endpoint, per window
API_CALL_WINDOW = 60
OTP_SEND_LIMIT = 3  # per patient, per window
OTP_SEND_WINDOW = 600


# --------------------------------------------------------------------------------------------------
# Guards
# --------------------------------------------------------------------------------------------------


def require_kiosk_user():
	"""Only a signed-in user holding the Kiosk role. Guests, staff and System Managers are refused."""
	if frappe.session.user == "Guest" or KIOSK_ROLE not in frappe.get_roles():
		frappe.throw(_("You do not have permission to use the kiosk."), frappe.PermissionError)


def get_settings():
	return frappe.get_single("Kiosk Settings")


def get_enabled_settings():
	settings = get_settings()
	if not settings.enabled:
		frappe.throw(_("Kiosk unavailable"), frappe.ValidationError)
	return settings


def log_deferred(title, detail):
	"""Log to Error Log through Redis (it survives the request's rollback), without the request's arguments.

	frappe.log_error also stores sanitized_dict(frappe.form_dict) in Error Log.metadata, and that only masks keys such
	as password, token or key. A kiosk request's arguments (name, dob, mobile, aadhaar, otp, ABHA address) would be
	stored in clear text, so the form_dict is swapped for an empty one for the duration of the call. Callers pass a
	title and a class name, never patient data. A logging failure (Redis down) never replaces the caller's answer.

	The guarantee covers the kiosk's own logging only, and holds with developer_mode off and the Frappe Recorder off:
	in developer_mode Frappe snapshots every exception with the real form_dict, and the Recorder stores it too.
	"""
	saved = None
	swapped = False
	try:
		saved = frappe.local.form_dict
		frappe.local.form_dict = frappe._dict()
		swapped = True
		frappe.log_error(title=title, message=detail, defer_insert=True)
	except Exception as e:
		try:
			frappe.logger("kiosk").error("%s: log failed (%s)", title, type(e).__name__)  # class only, no patient data
		except Exception:
			pass
	finally:
		if swapped:
			frappe.local.form_dict = saved


def refuse(title, detail, message):
	"""Answer a refused step with a generic message, after logging what refused (endpoint and exception class only).

	defer_insert: the request ends in an exception, and Frappe rolls the transaction back, which would take an
	Error Log row inserted here with it. A deferred log goes through Redis and survives.
	"""
	log_deferred(title, detail)
	frappe.throw(message, frappe.ValidationError)


def kiosk_endpoint(fn):
	"""Apply under @frappe.whitelist(): POST only, kiosk enabled, Kiosk role, and no internal detail in errors."""

	@functools.wraps(fn)
	def guarded(*args, **kwargs):
		try:
			require_kiosk_user()
			get_enabled_settings()
			return fn(*args, **kwargs)
		except (frappe.ValidationError, frappe.PermissionError, frappe.TooManyRequestsError):
			raise
		except Exception as e:
			# Class and endpoint only: a traceback would carry the request's locals, which hold patient data.
			refuse("Kiosk request failed", f"{fn.__name__}: {type(e).__name__}", _("Something went wrong. Please ask for help at the desk."))

	return require_post(guarded)


def _hit(name: str, limit: int, window: int):
	"""Count one hit in a fixed window; over the limit answers HTTP 429.

	Atomic (Redis INCR on the real prefixed key). The ABDM limiter in rate_limit.py is not reused: it reads a
	raw key but writes through frappe.cache.set_value, which prefixes and pickles, so it never sees its own count.
	"""
	key = frappe.cache.make_key(f"kiosk:rate:{name}")
	# One MULTI/EXEC: SET NX EX gives the key its expiry the moment it is created, so a worker dying between
	# the two calls can never leave a counter with no expiry (which would lock every kiosk out for good).
	pipe = frappe.cache.pipeline()
	pipe.set(key, 0, ex=window, nx=True)
	pipe.incr(key)
	count = pipe.execute()[-1]
	if count > limit:
		raise frappe.TooManyRequestsError(_("Too many requests. Please wait a minute and try again."))


def throttle(endpoint: str | None = None, otp_patient: str | None = None):
	"""10 calls a minute per endpoint, and 3 OTP sends per 10 minutes per patient.

	Every kiosk shares one user, so the limits are shared by all devices; a breach is a TooManyRequestsError (HTTP 429).
	"""
	if endpoint:
		_hit(f"{frappe.session.user}:{endpoint}", API_CALL_LIMIT, API_CALL_WINDOW)
	if otp_patient:
		_hit(f"{frappe.session.user}:otp:{otp_patient}", OTP_SEND_LIMIT, OTP_SEND_WINDOW)


@contextlib.contextmanager
def elevated():
	"""Run as Administrator, then restore the caller, even on error.

	The only way through AbhaClient._assert_patient_permission for a create-only Kiosk user. Call it only after
	assert_bound() has proved the patient belongs to this kiosk session.
	"""
	# frappe.set_user() also overwrites session.sid with the username and empties form_dict and session.data.
	# Put them back, or the session binding is looked up under the username instead of this device's sid.
	previous = frappe.session.user
	sid = frappe.session.sid
	form_dict = frappe.local.form_dict
	session_data = frappe.session.data
	frappe.set_user("Administrator")
	try:
		yield
	finally:
		frappe.set_user(previous)
		frappe.local.session.sid = sid
		frappe.local.form_dict = form_dict
		frappe.local.session.data = session_data


# --------------------------------------------------------------------------------------------------
# Camp
# --------------------------------------------------------------------------------------------------


def get_active_camp(settings):
	"""The configured camp when it is Ongoing and today falls in its dates; None when the kiosk is not camp-bound."""
	if settings.patient_source != "Medical Camp":
		return None

	camp = frappe.db.get_value(
		"Medical Camp",
		settings.medical_camp,
		["name", "camp_name", "status", "start_date", "end_date", "appointment_type", "company"],
		as_dict=True,
	)
	today = getdate(nowdate())
	if (
		not camp
		or camp.status != "Ongoing"
		or not camp.start_date
		or not camp.end_date
		or not (getdate(camp.start_date) <= today <= getdate(camp.end_date))
	):
		frappe.throw(_("This camp is not active. Please ask for help at the desk."), frappe.ValidationError)
	return camp


# --------------------------------------------------------------------------------------------------
# Session binding
# --------------------------------------------------------------------------------------------------
# Every kiosk shares one user, so the unit of identity is the login session (sid): the patients this
# session registered, and the OTP transactions it started. The client sends names; the server only
# honours the ones it handed out itself.


def _session_prefix():
	return f"kiosk:session:{frappe.session.sid}:"


def _ttl():
	return (get_settings().idle_timeout or 60) + SESSION_GRACE_SECONDS


# One key per patient and per transaction, so parallel requests (a double-tap, a print beside a booking)
# never overwrite each other's binding the way a single read-modify-write dict would.
def _patient_key(patient):
	return f"{_session_prefix()}patient:{patient}"


def _txn_key(txn_id):
	return f"{_session_prefix()}txn:{txn_id}"


def _touch(key):
	"""A session in use stays alive: a slow OTP or a long ABHA flow must not outlive its binding."""
	frappe.cache.expire_key(key, _ttl())


def bind_patient(patient: str, created: bool):
	key = _patient_key(patient)
	entry = frappe.cache.get_value(key, expires=True) or {}
	# created is sticky: a patient this session made stays eligible for ABHA even if re-submitted
	frappe.cache.set_value(key, {"created": bool(created or entry.get("created"))}, expires_in_sec=_ttl())


def assert_bound(patient: str, require_created: bool = False):
	"""Refuse a patient this kiosk session did not register (require_created) or at least match."""
	key = _patient_key(patient)
	entry = frappe.cache.get_value(key, expires=True)
	if not entry or (require_created and not entry["created"]):
		frappe.throw(_("This action is not available."), frappe.PermissionError)
	_touch(key)


def bind_txn(txn_id: str, patient: str):
	if not txn_id:
		return
	frappe.cache.set_value(_txn_key(txn_id), patient, expires_in_sec=_ttl())


def assert_txn_bound(txn_id: str, patient: str):
	"""Refuse an OTP transaction this session did not start for this patient (stops replay of a foreign txn)."""
	key = _txn_key(txn_id) if txn_id else None
	if not key or frappe.cache.get_value(key, expires=True) != patient:
		frappe.throw(_("This action is not available."), frappe.PermissionError)
	_touch(key)


def clear_session():
	frappe.cache.delete_keys(_session_prefix())
