"""Whitelisted API of the self-service registration kiosk (/kiosk).

Every endpoint is POST-only, needs the Kiosk role, and answers with the least it can: a UHID, a token, a
masked mobile. The client never chooses the patient source, the camp or the status; those come from Kiosk
Settings. Patients are created with ignore_permissions after an explicit create-permission check, because the
Kiosk role is create-only and the Patient's own hooks touch doctypes it cannot reach.
"""

import contextlib
import re

from redis.exceptions import LockError

import frappe
from frappe import _
from frappe.utils import add_years, getdate, nowdate, validate_email_address

from healthcare.api import kiosk_common as common

_MOBILE_RE = re.compile(r"^[6-9]\d{9}$")
_BAD_NAME_CHARS = re.compile(r"[<>{}\x00-\x1f]")
_MAX_NAME_LENGTH = 140
_MAX_AGE_YEARS = 130
_LOCK_TIMEOUT = 30  # a patient insert creates a Customer and runs several hooks; a lost lock lets a second request in


@frappe.whitelist(methods=["POST"])
@common.kiosk_endpoint
def get_config():
	return config_for(common.get_settings())


def config_for(settings):
	"""What the page needs to start: features, idle timeout and the camp's name (never its details)."""
	camp_name, camp_active = None, True
	if settings.patient_source == "Medical Camp":
		camp_name = frappe.db.get_value("Medical Camp", settings.medical_camp, "camp_name")
		try:
			common.get_active_camp(settings)
		except frappe.ValidationError:
			camp_active = False

	return {
		"idle_timeout": settings.idle_timeout,
		"enable_abha": bool(settings.enable_abha),
		"enable_appointment": bool(settings.enable_appointment) and camp_active and bool(camp_name),
		"camp": camp_name,
		"camp_active": camp_active,
	}


@frappe.whitelist(methods=["POST"])
@common.kiosk_endpoint
def end_session():
	"""Forget what this kiosk session registered. The page calls it on timeout or when a patient is done."""
	common.clear_session()
	return {}


@frappe.whitelist(methods=["POST"])
@common.kiosk_endpoint
def register_patient(
	first_name=None,
	last_name=None,
	sex=None,
	dob=None,
	mobile=None,
	email=None,
):
	"""Register a walk-up patient, or recognise one already registered, and issue today's token.

	Only the arguments above are accepted; Frappe drops any other request field before this runs.
	"""
	common.throttle("register_patient")
	settings = common.get_settings()
	camp = common.get_active_camp(settings)  # inactive camp: refuse rather than save the patient untagged

	data = _validate_demographics(first_name, last_name, sex, dob, mobile, email)

	lock_name = frappe.cache.make_key(f"kiosk:register:{data['dob']}:{data['mobile']}")
	with _locked_and_committed(lock_name):
		existing = _find_existing_patient(data["dob"], data["mobile"])
		if existing:
			patient = existing
			already_registered = True
		else:
			patient = _create_patient(data, settings, camp)
			already_registered = False

		token = _issue_token(patient, camp)
		# Inside the lock: a double-tap's second request cannot overwrite the sticky "created" flag afterwards
		common.bind_patient(patient.name, created=not already_registered)

	result = {
		"already_registered": already_registered,
		"masked_mobile": _mask_mobile(data["mobile"]),
		"token_number": token.token_number,
		"token": token.name,
		"camp": camp.camp_name if camp else None,
	}
	if already_registered:
		# Mobile + DOB is weak proof of identity, so a returning patient is greeted by first name only. Their
		# UHID and full name reach the screen never; they are on the printed slip, which the session may fetch.
		result["first_name"] = patient.first_name
	else:
		result.update({"uhid": patient.name, "patient_name": patient.patient_name})
	return result


@contextlib.contextmanager
def _locked_and_committed(lock_name):
	"""Serialise a check-then-insert across requests.

	The Redis lock only helps if the next holder can see this holder's rows, so the work is committed before
	the lock is released, and a first commit on entry ends this request's old DB snapshot (MariaDB's
	REPEATABLE READ would otherwise hide a row the previous holder committed a moment ago).
	"""
	try:
		with frappe.cache.lock(lock_name, timeout=_LOCK_TIMEOUT, blocking_timeout=_LOCK_TIMEOUT):
			frappe.db.commit()  # nothing is pending here; this only ends the old snapshot
			yield
			frappe.db.commit()
	except LockError as e:
		# Could not get the lock in time, or lost it while working (the work was committed first, so a retry
		# finds the patient or appointment already made). Say "busy" rather than the generic error.
		common.refuse("Kiosk lock failed", type(e).__name__, _("The kiosk is busy. Please try again in a moment."))


@frappe.whitelist(methods=["POST"])
@common.kiosk_endpoint
def get_token_slip(token=None):
	"""The printable slip, only for a token whose patient this kiosk session registered or matched.

	The slip prints the Kiosk Token, which holds the UHID, name, number, date and facility and nothing else,
	so rendering it needs no read access to the Patient.
	"""
	common.throttle("get_token_slip")
	token_row = _token_row(token)
	patient = token_row.patient if token_row else None
	if not patient:
		frappe.throw(_("This action is not available."), frappe.PermissionError)  # same answer as a foreign token
	common.assert_bound(patient)

	try:
		with common.elevated():  # printing checks the viewer's print permission on Kiosk Token, which Kiosk lacks
			html = frappe.get_print("Kiosk Token", token, "Patient Token Slip")
	except (frappe.ValidationError, frappe.PermissionError) as e:
		common.refuse("Kiosk slip refused", type(e).__name__, _("We could not print your slip. Please ask for help at the desk."))
	return {"html": html}


@frappe.whitelist(methods=["POST"])
@common.kiosk_endpoint
def book_appointment(token=None):
	"""Book today's camp appointment for the patient behind a token this session was issued.

	Only available at a camp kiosk with appointments enabled: the camp supplies the appointment type, so no
	practitioner or slot is chosen. Booking again the same day returns the appointment already made. The
	token, not the UHID, names the patient: a returning patient's UHID is never sent to the screen.
	"""
	common.throttle("book_appointment")
	token_row = _token_row(token)
	patient = token_row.patient if token_row else None
	if not patient:
		frappe.throw(_("This action is not available."), frappe.PermissionError)
	common.assert_bound(patient)

	settings = common.get_settings()
	if not settings.enable_appointment or settings.patient_source != "Medical Camp":
		frappe.throw(_("Appointment booking is not available."), frappe.ValidationError)
	camp = common.get_active_camp(settings)
	if not camp.appointment_type:
		frappe.throw(_("Appointment booking is not available."), frappe.ValidationError)

	today = nowdate()
	lock_name = frappe.cache.make_key(f"kiosk:book:{patient}:{today}")
	with _locked_and_committed(lock_name):
		appointment = _todays_camp_appointment(patient, camp, today) or _create_camp_appointment(patient, camp, today)

	return {
		"appointment": appointment.name,
		"date": str(appointment.appointment_date),
		"time": str(appointment.appointment_time) if appointment.appointment_time else None,
		"token_number": token_row.token_number,
	}


def _token_row(token):
	if not isinstance(token, str) or not token:
		return None
	return frappe.db.get_value("Kiosk Token", token, ["patient", "token_number"], as_dict=True)


def _todays_camp_appointment(patient, camp, today):
	return frappe.db.get_value(
		"Patient Appointment",
		{
			"patient": patient,
			"medical_camp": camp.name,
			"appointment_date": today,
			"status": ("!=", "Cancelled"),
		},
		["name", "appointment_date", "appointment_time"],
		as_dict=True,
	)


def _require_create(doctype, what):
	"""The Kiosk role's create permission, checked explicitly because the insert itself ignores permissions."""
	if not frappe.has_permission(doctype, "create"):
		common.refuse("Kiosk permission refused", doctype, _("We could not {0}. Please ask for help at the desk.").format(what))


def _create_camp_appointment(patient, camp, date):
	_require_create("Patient Appointment", _("book your appointment"))
	doc = frappe.get_doc(
		{
			"doctype": "Patient Appointment",
			"patient": patient,
			"appointment_for": "Medical Camp",
			"medical_camp": camp.name,
			"appointment_type": camp.appointment_type,
			"appointment_date": date,
			"company": camp.company,
		}
	)
	try:
		doc.insert(ignore_permissions=True)
	except (frappe.ValidationError, frappe.DuplicateEntryError) as e:
		common.refuse("Kiosk appointment refused", type(e).__name__, _("We could not book your appointment. Please ask for help at the desk."))
	return doc


# --------------------------------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------------------------------


def _validate_demographics(first_name, last_name, sex, dob, mobile, email):
	return {
		"first_name": _clean_name(first_name, required=True),
		"last_name": _clean_name(last_name, required=False),
		"sex": _clean_sex(sex),
		"dob": _clean_dob(dob),
		"mobile": normalise_mobile(mobile),
		"email": _clean_email(email),
	}


def _stripped(value):
	return value.strip() if isinstance(value, str) else ""


def _clean_name(value, required):
	value = _stripped(value)
	if not value:
		if required:
			frappe.throw(_("Please enter your name."), frappe.ValidationError)
		return None
	if len(value) > _MAX_NAME_LENGTH or _BAD_NAME_CHARS.search(value):
		frappe.throw(_("Please enter a valid name."), frappe.ValidationError)
	return value


def _clean_sex(value):
	value = _stripped(value)
	canonical = frappe.db.get_value("Gender", value, "name") if value else None
	if not canonical:
		frappe.throw(_("Please select your gender."), frappe.ValidationError)
	return canonical


def _clean_dob(value):
	try:
		dob = getdate(value) if value else None  # getdate("") is today, which would pass as a birth date
	except Exception:
		dob = None
	if not dob or dob > getdate(nowdate()) or dob < add_years(getdate(nowdate()), -_MAX_AGE_YEARS):
		frappe.throw(_("Please enter a valid date of birth."), frappe.ValidationError)
	return str(dob)


def normalise_mobile(value):
	"""Ten digits as dialled in India: +91 / 91 / 0 prefixes, spaces and dashes dropped."""
	digits = re.sub(r"[\s\-()]", "", value) if isinstance(value, str) else ""
	if digits.startswith("+91"):
		digits = digits[3:]
	elif len(digits) == 12 and digits.startswith("91"):
		digits = digits[2:]
	elif len(digits) == 11 and digits.startswith("0"):
		digits = digits[1:]
	if not _MOBILE_RE.match(digits):
		frappe.throw(_("Please enter a valid 10 digit mobile number."), frappe.ValidationError)
	return digits


def _clean_email(value):
	value = _stripped(value)
	if not value:
		return None
	if not validate_email_address(value):
		frappe.throw(_("Please enter a valid email address."), frappe.ValidationError)
	return value


def _mask_mobile(mobile):
	return "X" * 6 + mobile[-4:]


# --------------------------------------------------------------------------------------------------
# Patient and token
# --------------------------------------------------------------------------------------------------


def _find_existing_patient(dob, mobile):
	"""Same DOB and the same last ten digits of mobile. Runs whether or not Healthcare Settings'
	duplicate check is on, so a double-tap or a returning patient never gets a second record."""
	for row in frappe.get_all("Patient", filters={"dob": dob}, fields=["name", "first_name", "patient_name", "mobile"]):
		if _last_ten_digits(row.mobile) == mobile:
			return row
	return None


def _last_ten_digits(value):
	return re.sub(r"\D", "", value or "")[-10:]


def _create_patient(data, settings, camp):
	_require_create("Patient", _("register you here"))

	doc = frappe.get_doc(
		{
			"doctype": "Patient",
			**{key: value for key, value in data.items() if value},
			# Server-decided, never taken from the request
			"patient_source": settings.patient_source,
			"medical_camp": camp.name if camp else None,
		}
	)
	try:
		doc.insert(ignore_permissions=True)
	except frappe.ValidationError as e:
		# Healthcare Settings' own duplicate check can refuse; say nothing about the other record to the patient
		common.refuse("Kiosk patient insert refused", type(e).__name__, _("We could not register you here. Please ask for help at the desk."))
	return doc


def _issue_token(patient, camp):
	"""Today's token for the patient: the one already issued if they register again, else the next number.

	A unique (patient, token_date) index backs the lock: should two requests still race, the loser reads
	the winner's token instead of issuing a second one.
	"""
	today = nowdate()
	existing = _todays_token(patient.name, today)
	if existing:
		return existing

	company = frappe.defaults.get_defaults().company
	doc = frappe.get_doc(
		{
			"doctype": "Kiosk Token",
			"patient": patient.name,
			"patient_name": patient.patient_name,
			"token_date": today,
			"medical_camp": camp.name if camp else None,
			"facility_name": camp.camp_name if camp else company,
		}
	)
	try:
		return doc.insert(ignore_permissions=True)
	except (frappe.DuplicateEntryError, frappe.UniqueValidationError):
		frappe.db.rollback()
		frappe.clear_messages()  # the failed insert queued a "must be unique" message for the screen
		winner = _todays_token(patient.name, today)
		if not winner:
			raise
		return winner
	except frappe.ValidationError as e:
		common.refuse("Kiosk token refused", type(e).__name__, _("We could not issue your token. Please ask for help at the desk."))


def _todays_token(patient, today):
	name = frappe.db.get_value("Kiosk Token", {"patient": patient, "token_date": today})
	return frappe.get_doc("Kiosk Token", name) if name else None
