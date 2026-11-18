"""Whitelisted API of the self-service registration kiosk (/kiosk).

Every endpoint is POST-only, needs the Kiosk role, and answers with the least it can: a UHID, a token, a
masked mobile. The client never chooses the patient source, the camp or the status; those come from Kiosk
Settings. Patients are created with ignore_permissions after an explicit create-permission check, because the
Kiosk role is create-only and the Patient's own hooks touch doctypes it cannot reach.
"""

import contextlib
import hashlib
import re
import sys

from redis.exceptions import LockError

import frappe
from frappe import _
from frappe.utils import add_years, getdate, nowdate, validate_email_address

from healthcare.api import kiosk_common as common

_MOBILE_RE = re.compile(r"^[6-9]\d{9}$")
_ABHA_ADDRESS_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}@[a-z]{2,10}$")
_ABHA_NUMBER_RE = re.compile(r"^\d{14}$")
_BAD_NAME_CHARS = re.compile(r"[<>{}\x00-\x1f]")
_MAX_NAME_LENGTH = 140
_MAX_AGE_YEARS = 130
# A mobile can be shared by a family; more matches than this is not a list a person can pick from
_MAX_MATCHES = 10
_SEARCH_PREFILTER_LIMIT = 200
# A patient insert creates a Customer and runs several hooks; a lost lock lets a second request in
_LOCK_TIMEOUT = 30


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
		"hospital": settings.hospital_name or _default_company_name(),
		"idle_timeout": settings.idle_timeout,
		"enable_abha": bool(settings.enable_abha),
		"enable_appointment": bool(settings.enable_appointment) and camp_active and bool(camp_name),
		"camp": camp_name,
		"camp_active": camp_active,
	}


def _default_company_name():
	"""The name of the default company, if the site has one; blank hides the header."""
	return frappe.defaults.get_defaults().company or ""


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

	# Hashed: a Redis key name is visible to MONITOR and KEYS, and must not carry the patient's DOB and mobile
	lock_name = frappe.cache.make_key(f"kiosk:register:{_digest(data['dob'], data['mobile'])}")
	with _locked_and_committed(lock_name):
		existing = _find_existing_patient(data["dob"], data["mobile"])
		if existing:
			patient = existing
			already_registered = True
		else:
			patient = _create_patient(data, settings, camp)
			already_registered = False

		# A token race that is really lost ends in _issue_token's generic refusal: the rollback takes this
		# request's new patient with it, so there is nothing to bind and no other patient is ever adopted.
		token = _issue_token(patient, camp)
		# Inside the lock: a double-tap's second request cannot overwrite the sticky "created" flag afterwards
		common.bind_patient(patient.name, created=not already_registered, selected=already_registered)

	if already_registered:
		# Same footing as picking a name from the mobile search: the clinic accepts the number as proof
		return _returning_result(patient, token, camp)
	return {
		"already_registered": False,
		"uhid": patient.name,
		"patient_name": patient.patient_name,
		"masked_mobile": _mask_mobile(data["mobile"]),
		"token_number": token.token_number,
		"token": token.name,
		"camp": camp.camp_name if camp else None,
	}


@frappe.whitelist(methods=["POST"])
@common.kiosk_endpoint
def search_by_mobile(mobile=None):
	"""Names registered under a mobile number, for the patient to pick their own from.

	Mobile alone is the proof here, by the clinic's choice: the list carries names and ages only, each with an
	opaque reference good for this session. No UHID, DOB, address or ABHA reaches the screen until one is picked.
	"""
	common.throttle("search_by_mobile")
	mobile = normalise_mobile(mobile)
	rows = frappe.get_all(
		"Patient",
		filters={"mobile": ("like", f"%{mobile[-4:]}")},
		fields=["name", "patient_name", "dob", "mobile"],
		order_by="patient_name",
		limit=_SEARCH_PREFILTER_LIMIT,
	)
	matches = [row for row in rows if _last_ten_digits(row.mobile) == mobile][:_MAX_MATCHES]
	return _match_list(matches)


@frappe.whitelist(methods=["POST"])
@common.kiosk_endpoint
def search_by_abha(abha=None):
	"""The same list, found by an ABHA address (name@sbx) or a 14 digit ABHA number instead of a mobile.

	A local lookup of ABHA Records already linked at this clinic; ABDM is not called. The proof is the same as
	for the mobile: knowing the identifier. Nothing is revealed until the patient picks their own name.
	"""
	common.throttle("search_by_abha")
	field, value = _clean_abha(abha)
	patients = frappe.get_all("ABHA Record", filters={field: value}, pluck="patient", limit=_MAX_MATCHES)
	if not patients:
		return {"matches": []}
	rows = frappe.get_all(
		"Patient",
		filters={"name": ("in", patients)},
		fields=["name", "patient_name", "dob"],
		order_by="patient_name",
	)
	return _match_list(rows)


def _match_list(rows):
	"""Names and ages behind opaque, session-bound references; never the UHID."""
	return {
		"matches": [
			{"ref": common.offer_candidate(row.name), "name": row.patient_name, "age": _age_text(row.dob)}
			for row in rows
		]
	}


def _clean_abha(value):
	"""(ABHA Record field, value) for an ABHA address or a 14 digit ABHA number."""
	text = _stripped(value).lower()
	if "@" in text:
		if _ABHA_ADDRESS_RE.match(text):
			return "abha_address", text
	else:
		digits = re.sub(r"[\s-]", "", text)
		if _ABHA_NUMBER_RE.match(digits):
			return "abha_number", digits
	frappe.throw(_("Please enter a valid ABHA address or 14 digit ABHA number."), frappe.ValidationError)


@frappe.whitelist(methods=["POST"])
@common.kiosk_endpoint
def select_patient(ref=None):
	"""The patient picked from the mobile search: show their limited details and issue today's token."""
	common.throttle("select_patient")
	settings = common.get_settings()
	camp = common.get_active_camp(settings)
	name = common.resolve_candidate(ref)

	lock_name = frappe.cache.make_key(f"kiosk:select:{_digest(name)}")
	with _locked_and_committed(lock_name):
		patient = frappe.db.get_value(
			"Patient", name, ["name", "patient_name", "sex", "dob", "mobile"], as_dict=True
		)
		if not patient:
			frappe.throw(_("This action is not available."), frappe.PermissionError)
		token = _issue_token(patient, camp)
		common.bind_patient(patient.name, created=False, selected=True)

	return _returning_result(patient, token, camp)


def _returning_result(patient, token, camp):
	"""What the screen shows for a patient who was already registered: limited details and today's token.

	The UHID is needed by the ABHA steps. Never DOB, address, email or the full mobile.
	"""
	return {
		"already_registered": True,
		"patient_name": patient.patient_name,
		"uhid": patient.name,
		"sex": patient.sex,
		"age": _age_text(patient.dob),
		"masked_mobile": _mask_mobile(_last_ten_digits(patient.mobile)),
		"abha_linked": bool(frappe.db.exists("ABHA Record", {"patient": patient.name})),
		"token_number": token.token_number,
		"token": token.name,
		"camp": camp.camp_name if camp else None,
	}


def _age_text(dob):
	if not dob:
		return None
	today = getdate(nowdate())
	dob = getdate(dob)
	return str(today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day)))


def _digest(*parts):
	return hashlib.sha256(":".join(str(p) for p in parts).encode()).hexdigest()


@contextlib.contextmanager
def _locked_and_committed(lock_name):
	"""Serialise a check-then-insert across requests.

	The Redis lock only helps if the next holder can see this holder's rows, so the work is committed before
	the lock is released, and a first commit on entry ends this request's old DB snapshot (MariaDB's
	REPEATABLE READ would otherwise hide a row the previous holder committed a moment ago).

	Only failing to get the lock says "busy". Once the work is committed, a lock that has expired or is no longer
	owned at release is logged and nothing more: telling the patient to retry would undo a finished registration.
	"""
	try:
		lock = frappe.cache.lock(lock_name, timeout=_LOCK_TIMEOUT, blocking_timeout=_LOCK_TIMEOUT)
		lock.__enter__()
	except LockError as e:
		common.refuse(
			"Kiosk lock failed", type(e).__name__, _("The kiosk is busy. Please try again in a moment.")
		)

	try:
		frappe.db.commit()  # nothing is pending here; this only ends the old snapshot
		yield
		frappe.db.commit()
	except BaseException:
		_release(lock, *sys.exc_info())
		raise  # the real failure, never replaced by a release problem
	else:
		_release(lock, None, None, None)


def _release(lock, *exc_info):
	try:
		lock.__exit__(*exc_info)
	except LockError as e:
		common.log_deferred("Kiosk lock lost", type(e).__name__)


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
		# same answer as a foreign token
		frappe.throw(_("This action is not available."), frappe.PermissionError)
	common.assert_bound(patient)

	try:
		# printing checks the viewer's print permission on Kiosk Token, which Kiosk lacks
		with common.elevated():
			html = frappe.get_print("Kiosk Token", token, "Patient Token Slip")
	except (frappe.ValidationError, frappe.PermissionError) as e:
		common.refuse(
			"Kiosk slip refused",
			type(e).__name__,
			_("We could not print your slip. Please ask for help at the desk."),
		)
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
	lock_name = frappe.cache.make_key(f"kiosk:book:{_digest(patient, today)}")
	with _locked_and_committed(lock_name):
		appointment = _todays_camp_appointment(patient, camp, today) or _create_camp_appointment(
			patient, camp, today
		)

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
		common.refuse(
			"Kiosk permission refused",
			doctype,
			_("We could not {0}. Please ask for help at the desk.").format(what),
		)


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
		common.refuse(
			"Kiosk appointment refused",
			type(e).__name__,
			_("We could not book your appointment. Please ask for help at the desk."),
		)
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
	for row in frappe.get_all(
		"Patient", filters={"dob": dob}, fields=["name", "patient_name", "sex", "dob", "mobile"]
	):
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
		common.refuse(
			"Kiosk patient insert refused",
			type(e).__name__,
			_("We could not register you here. Please ask for help at the desk."),
		)
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
			common.refuse(
				"Kiosk token refused",
				"unique violation, no winner",
				_("We could not issue your token. Please ask for help at the desk."),
			)
		return winner
	except frappe.ValidationError as e:
		common.refuse(
			"Kiosk token refused",
			type(e).__name__,
			_("We could not issue your token. Please ask for help at the desk."),
		)


def _todays_token(patient, today):
	name = frappe.db.get_value("Kiosk Token", {"patient": patient, "token_date": today})
	return frappe.get_doc("Kiosk Token", name) if name else None
