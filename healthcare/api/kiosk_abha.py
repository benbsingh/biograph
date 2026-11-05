"""Kiosk wrappers around the existing ABDM M1 flows (create an ABHA by Aadhaar or mobile OTP, or link one).

The M1 endpoints need write permission on the Patient (AbhaClient._assert_patient_permission), which the
create-only Kiosk role does not have, and that guard stays exactly as it is. These wrappers instead:

  1. work only on a patient THIS kiosk session registered (a matched, already-registered patient is refused,
     because mobile + DOB is weak proof of who is standing at the screen),
  2. accept only OTP transactions this session started, so a foreign txn cannot be replayed,
  3. run the unchanged M1 function as Administrator for that one call (common.elevated), and
  4. answer with an allow-list: a txn id, a status, masked values. No token, profile, full ABHA number or
     Aadhaar ever leaves the server through here, and M1's own error text (which can name other patients)
     is replaced by a generic message.
"""

import re

import frappe
from frappe import _

from healthcare.api import kiosk_common as common
from healthcare.regional.india.abdm.api import enrol, profile

_OTP_RE = re.compile(r"^\d{6}$")
_AADHAAR_RE = re.compile(r"^\d{12}$")
_MOBILE_RE = re.compile(r"^\d{10}$")
_ABHA_ADDRESS_RE = re.compile(r"^[A-Za-z0-9._]{3,64}@(abdm|sbx)$")
_OTP_SYSTEMS = ("abdm", "aadhaar")
_UNAVAILABLE = "ABHA is not available."


# --------------------------------------------------------------------------------------------------
# Create by Aadhaar OTP
# --------------------------------------------------------------------------------------------------


@frappe.whitelist()
@common.kiosk_endpoint
def send_aadhaar_otp(patient=None, aadhaar=None):
	aadhaar = _digits(aadhaar, _AADHAAR_RE, _("Please enter your 12 digit Aadhaar number."))
	result = _run("abha_send_aadhaar_otp", patient, lambda: enrol.generate_aadhaar_otp(patient, aadhaar), otp_send=True)
	return {"txn_id": _bind_txn(result.get("txnId"), patient)}


@frappe.whitelist()
@common.kiosk_endpoint
def verify_aadhaar_otp(patient=None, txn_id=None, otp=None, mobile=None):
	"""Verify the Aadhaar OTP and create the ABHA. `mobile_linked` False means a mobile OTP step follows."""
	otp = _digits(otp, _OTP_RE, _("Please enter the 6 digit OTP."))
	mobile = _digits(mobile, _MOBILE_RE, _("Please enter your 10 digit mobile number.")) if mobile else ""
	result = _run(
		"abha_verify_aadhaar_otp", patient, lambda: enrol.enrol_by_aadhaar(patient, txn_id, otp, mobile), txn_id=txn_id
	)
	return {
		"status": result.get("status"),
		"mobile_linked": bool(result.get("mobile_linked")),
		"masked_abha_number": _mask_abha_number(result.get("abha_number")),
		"txn_id": _bind_txn(result.get("txnId"), patient),
	}


@frappe.whitelist()
@common.kiosk_endpoint
def send_enrol_mobile_otp(patient=None, txn_id=None, mobile=None):
	"""When the mobile given differs from the Aadhaar-linked one ABDM sends a second OTP to verify it."""
	mobile = _digits(mobile, _MOBILE_RE, _("Please enter your 10 digit mobile number."))
	result = _run(
		"abha_send_mobile_otp",
		patient,
		lambda: enrol.send_mobile_otp(patient, txn_id, mobile),
		txn_id=txn_id,
		otp_send=True,
	)
	return {"txn_id": _bind_txn(result.get("txnId"), patient) or txn_id}


@frappe.whitelist()
@common.kiosk_endpoint
def verify_enrol_mobile_otp(patient=None, txn_id=None, otp=None):
	otp = _digits(otp, _OTP_RE, _("Please enter the 6 digit OTP."))
	result = _run(
		"abha_verify_mobile_otp", patient, lambda: enrol.verify_mobile_otp(patient, txn_id, otp), txn_id=txn_id
	)
	return {"status": "verified" if result else None, "txn_id": txn_id}


# --------------------------------------------------------------------------------------------------
# Create by mobile OTP
# --------------------------------------------------------------------------------------------------


@frappe.whitelist()
@common.kiosk_endpoint
def send_mobile_otp_create(patient=None, mobile=None):
	mobile = _digits(mobile, _MOBILE_RE, _("Please enter your 10 digit mobile number."))
	result = _run("abha_send_create_otp", patient, lambda: enrol.generate_mobile_otp(patient, mobile), otp_send=True)
	return {"txn_id": _bind_txn(result.get("txnId"), patient)}


@frappe.whitelist()
@common.kiosk_endpoint
def verify_mobile_otp_create(patient=None, txn_id=None, otp=None):
	otp = _digits(otp, _OTP_RE, _("Please enter the 6 digit OTP."))
	result = _run(
		"abha_verify_create_otp", patient, lambda: enrol.verify_mobile_otp_enrol(patient, txn_id, otp), txn_id=txn_id
	)
	return {"txn_id": _bind_txn(result.get("txnId"), patient) or txn_id}


# --------------------------------------------------------------------------------------------------
# Choose an ABHA address (after either create flow)
# --------------------------------------------------------------------------------------------------


@frappe.whitelist()
@common.kiosk_endpoint
def get_address_suggestions(patient=None):
	result = _run("abha_address_suggestions", patient, lambda: enrol.get_abha_suggestions(patient))
	suggestions = result.get("suggestions") or []
	return {"suggestions": [s for s in suggestions if isinstance(s, str) and _ABHA_ADDRESS_RE.match(s)]}


@frappe.whitelist()
@common.kiosk_endpoint
def choose_address(patient=None, abha_address=None):
	abha_address = (abha_address or "").strip() if isinstance(abha_address, str) else ""
	if not _ABHA_ADDRESS_RE.match(abha_address):
		frappe.throw(_("Please choose a valid ABHA address."), frappe.ValidationError)
	result = _run("abha_choose_address", patient, lambda: enrol.set_abha_address(patient, abha_address))
	return {"status": "saved" if result else None, "masked_abha_address": _mask_address(abha_address)}


# --------------------------------------------------------------------------------------------------
# Link an ABHA the patient already has
# --------------------------------------------------------------------------------------------------


@frappe.whitelist()
@common.kiosk_endpoint
def send_link_otp(patient=None, abha_address=None, otp_system="abdm"):
	abha_address = (abha_address or "").strip() if isinstance(abha_address, str) else ""
	if not _ABHA_ADDRESS_RE.match(abha_address):
		frappe.throw(_("Please enter a valid ABHA address."), frappe.ValidationError)
	otp_system = _otp_system(otp_system)
	result = _run(
		"abha_send_link_otp",
		patient,
		lambda: profile.request_abha_address_otp(patient, abha_address, otp_system),
		otp_send=True,
	)
	return {"txn_id": _bind_txn(result.get("txnId"), patient)}


@frappe.whitelist()
@common.kiosk_endpoint
def verify_link_otp(patient=None, txn_id=None, otp=None, otp_system="abdm"):
	"""Verify the OTP and link the ABHA. M1 returns the whole profile; only masked identifiers go back."""
	otp = _digits(otp, _OTP_RE, _("Please enter the 6 digit OTP."))
	otp_system = _otp_system(otp_system)
	result = _run(
		"abha_verify_link_otp",
		patient,
		lambda: profile.verify_abha_address_otp(patient, txn_id, otp, otp_system),
		txn_id=txn_id,
	)
	return {
		"status": "linked",
		"masked_abha_number": _mask_abha_number(result.get("abha_number")),
		"masked_abha_address": _mask_address(result.get("abha_address")),
	}


# --------------------------------------------------------------------------------------------------
# Plumbing
# --------------------------------------------------------------------------------------------------


def _run(endpoint, patient, call, txn_id=None, otp_send=False):
	"""Gate, then run one M1 call as Administrator, and nothing else."""
	if not isinstance(patient, str) or not patient:
		frappe.throw(_("This action is not available."), frappe.PermissionError)
	common.throttle(endpoint)
	if not common.get_settings().enable_abha:
		frappe.throw(_(_UNAVAILABLE), frappe.ValidationError)
	common.assert_bound(patient, require_created=True)
	if txn_id is not None:
		common.assert_txn_bound(txn_id, patient)
	if otp_send:
		# After the binding checks, so a stranger guessing UHIDs cannot use up a real patient's OTP allowance
		common.throttle(otp_patient=patient)

	try:
		with common.elevated():
			return call() or {}
	except frappe.ValidationError as e:
		# M1's text can name another patient's UHID or an ABHA number; log it for staff, tell the patient little
		frappe.log_error(title="Kiosk ABHA step refused", message=f"{endpoint}: {str(e)[:200]}")
		frappe.throw(
			_("We could not complete this step. Please check what you entered, or ask for help at the desk."),
			frappe.ValidationError,
		)


def _bind_txn(txn_id, patient):
	if isinstance(txn_id, str) and txn_id:
		common.bind_txn(txn_id, patient)
		return txn_id
	return None


def _digits(value, pattern, message):
	cleaned = re.sub(r"[\s-]", "", value) if isinstance(value, str) else ""
	if not pattern.match(cleaned):
		frappe.throw(message, frappe.ValidationError)
	return cleaned


def _otp_system(value):
	value = (value or "abdm").lower() if isinstance(value, str) else ""
	if value not in _OTP_SYSTEMS:
		frappe.throw(_("Please choose how to receive the OTP."), frappe.ValidationError)
	return value


def _mask_abha_number(value):
	digits = re.sub(r"\D", "", value or "")
	return "XX-XXXX-XXXX-" + digits[-4:] if len(digits) >= 4 else None


def _mask_address(value):
	if not isinstance(value, str) or "@" not in value:
		return None
	name, domain = value.rsplit("@", 1)
	return f"{name[:2]}{'*' * max(len(name) - 2, 1)}@{domain}"
