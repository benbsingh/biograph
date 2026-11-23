"""Kiosk wrappers around the existing ABDM M1 flows (create an ABHA by Aadhaar OTP, or link one).

The M1 endpoints need write permission on the Patient (AbhaClient._assert_patient_permission), which the
create-only Kiosk role does not have, and that guard stays exactly as it is. These wrappers instead:

  1. work only on a patient THIS kiosk session registered (a matched, already-registered patient is refused,
     because mobile + DOB is weak proof of who is standing at the screen),
  2. accept only OTP transactions this session started, so a foreign txn cannot be replayed,
  3. send a create-ABHA OTP only after this session recorded the patient's agreement to ABDM's terms
     (record_consent here, checked again by the same rule as the desk), and
  4. run the unchanged M1 function as Administrator for that one call (common.elevated), and
  5. answer with an allow-list: a txn id, a status, masked values. No token, profile, full ABHA number or
     Aadhaar ever leaves the server through here, and M1's own error text (which can name other patients)
     is replaced by a generic message.

Known limitation: the M1 code (regional/, deliberately untouched here) writes ABDM response bodies and exception
text to Error Log through frappe.log_error. Those can echo the Aadhaar, mobile or ABHA value a kiosk sent, and a
Healthcare Administrator can read them. The request arguments themselves are not stored (elevated() empties the
form_dict). Scrubbing those M1 log calls is a follow-up outside this change.
"""

import re

import frappe
from frappe import _

from healthcare.api import kiosk_common as common
from healthcare.regional.india.abdm.api import consent, enrol, profile, ui

_OTP_RE = re.compile(r"^\d{6}$")
_AADHAAR_RE = re.compile(r"^\d{12}$")
_MOBILE_RE = re.compile(r"^\d{10}$")
_ABHA_ADDRESS_RE = re.compile(r"^[A-Za-z0-9._]{3,64}@(abdm|sbx)$")
_OTP_SYSTEMS = ("abdm", "aadhaar")


# --------------------------------------------------------------------------------------------------
# Agreement to ABDM's terms: shown and recorded before any create-ABHA OTP is sent
# --------------------------------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
@common.kiosk_endpoint
def get_consent_text(patient=None):
	"""ABDM's own wording, exactly as record_consent stores it, so the screen shows what is recorded."""
	common.throttle("abha_get_consent_text")
	if not common.get_settings().enable_abha:
		frappe.throw(_("ABHA is not available."), frappe.ValidationError)
	_assert_own_new_patient(patient)  # also keeps this patient's access alive while the terms are on screen
	text = consent.get_consent_text()
	return {key: text.get(key) for key in ("title", "text", "version")}


@frappe.whitelist(methods=["POST"])
@common.kiosk_endpoint
def keep_alive(patient=None):
	"""Refresh this session's access to a patient it registered, while that patient is reading the terms.

	The screen calls it only on a touch, key press or scroll, so a patient who walks away stops refreshing it
	and the access lapses on its normal idle timeout. It does nothing for a patient this session did not register.
	"""
	_assert_own_new_patient(patient)
	return {}


def _assert_own_new_patient(patient):
	if not isinstance(patient, str) or not patient:
		frappe.throw(_("This action is not available."), frappe.PermissionError)
	common.assert_bound(patient, require_created=True)  # refreshes the binding's expiry as well


@frappe.whitelist(methods=["POST"])
@common.kiosk_endpoint
def record_consent(patient=None):
	"""Record that this patient agreed to the terms. Sending an OTP to create an ABHA refuses until this is done."""
	record = _run(
		"abha_record_consent",
		patient,
		lambda: consent.record_consent(patient, consent_type="ABHA Creation", source="Kiosk"),
	)
	common.bind_consent(patient, record["consent_name"])
	return {"recorded": True}


def _send_after_consent(endpoint, patient, send, auth_method):
	"""Send a create-ABHA OTP only on top of this session's recorded agreement, then link it to the transaction."""
	# Read before common.elevated(), which swaps the session id this lookup is keyed on
	consent_name = common.bound_consent(patient)

	def checked_send():
		ui._check_consent(patient, consent_name)
		result = send()
		if result.get("txnId"):
			frappe.db.set_value(
				"ABDM Consent",
				consent_name,
				{"txn_id": result["txnId"], "auth_method": auth_method},
				update_modified=False,
			)
		return result

	return _run(endpoint, patient, checked_send, otp_send=True)


# --------------------------------------------------------------------------------------------------
# Create by Aadhaar OTP
# --------------------------------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
@common.kiosk_endpoint
def send_aadhaar_otp(patient=None, aadhaar=None):
	aadhaar = _digits(aadhaar, _AADHAAR_RE, _("Please enter your 12 digit Aadhaar number."))
	result = _send_after_consent(
		"abha_send_aadhaar_otp", patient, lambda: enrol.generate_aadhaar_otp(patient, aadhaar), "Aadhaar OTP"
	)
	return {"txn_id": _bind_txn(result.get("txnId"), patient)}


@frappe.whitelist(methods=["POST"])
@common.kiosk_endpoint
def verify_aadhaar_otp(patient=None, txn_id=None, otp=None, mobile=None):
	"""Verify the Aadhaar OTP and create the ABHA. `mobile_linked` False means a mobile OTP step follows."""
	otp = _digits(otp, _OTP_RE, _("Please enter the 6 digit OTP."))
	mobile = _digits(mobile, _MOBILE_RE, _("Please enter your 10 digit mobile number.")) if mobile else ""
	result = _run(
		"abha_verify_aadhaar_otp",
		patient,
		lambda: enrol.enrol_by_aadhaar(patient, txn_id, otp, mobile),
		txn_id=txn_id,
	)
	return {
		"status": result.get("status"),
		"mobile_linked": bool(result.get("mobile_linked")),
		"masked_abha_number": _mask_abha_number(result.get("abha_number")),
		"txn_id": _bind_txn(result.get("txnId"), patient),
	}


@frappe.whitelist(methods=["POST"])
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


@frappe.whitelist(methods=["POST"])
@common.kiosk_endpoint
def verify_enrol_mobile_otp(patient=None, txn_id=None, otp=None):
	otp = _digits(otp, _OTP_RE, _("Please enter the 6 digit OTP."))
	result = _run(
		"abha_verify_mobile_otp",
		patient,
		lambda: enrol.verify_mobile_otp(patient, txn_id, otp),
		txn_id=txn_id,
	)
	return {"status": "verified" if result else None, "txn_id": txn_id}


# --------------------------------------------------------------------------------------------------
# Choose an ABHA address (after the create flow)
# --------------------------------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
@common.kiosk_endpoint
def get_address_suggestions(patient=None):
	result = _run("abha_address_suggestions", patient, lambda: enrol.get_abha_suggestions(patient))
	suggestions = result.get("suggestions") or []
	return {"suggestions": [s for s in suggestions if isinstance(s, str) and _ABHA_ADDRESS_RE.match(s)]}


@frappe.whitelist(methods=["POST"])
@common.kiosk_endpoint
def choose_address(patient=None, abha_address=None):
	abha_address = _abha_address(abha_address, _("Please choose a valid ABHA address."))
	result = _run("abha_choose_address", patient, lambda: enrol.set_abha_address(patient, abha_address))
	return {"status": "saved" if result else None, "masked_abha_address": _mask_address(abha_address)}


# --------------------------------------------------------------------------------------------------
# Link an ABHA the patient already has
# --------------------------------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
@common.kiosk_endpoint
def send_link_otp(patient=None, abha_address=None, otp_system="abdm"):
	abha_address = _abha_address(abha_address, _("Please enter a valid ABHA address."))
	otp_system = _otp_system(otp_system)
	result = _run(
		"abha_send_link_otp",
		patient,
		lambda: profile.request_abha_address_otp(patient, abha_address, otp_system),
		otp_send=True,
	)
	return {"txn_id": _bind_txn(result.get("txnId"), patient)}


@frappe.whitelist(methods=["POST"])
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
		frappe.throw(_("ABHA is not available."), frappe.ValidationError)
	common.assert_bound(patient, require_created=True)
	if txn_id is not None:
		common.assert_txn_bound(txn_id, patient)
	if otp_send:
		# After the binding checks, so a stranger guessing UHIDs cannot use up a real patient's OTP allowance
		common.throttle(otp_patient=patient)

	try:
		with common.elevated():
			return call() or {}
	except (frappe.ValidationError, frappe.PermissionError) as e:
		# M1's text can name another patient's UHID or an ABHA number, so neither the screen nor the log gets
		# it: the endpoint and the exception class tell staff which step was refused.
		common.refuse(
			"Kiosk ABHA step refused",
			f"{endpoint}: {type(e).__name__}",
			_("We could not complete this step. Please check what you entered, or ask for help at the desk."),
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


def _abha_address(value, message):
	value = value.strip() if isinstance(value, str) else ""
	if not _ABHA_ADDRESS_RE.match(value):
		frappe.throw(message, frappe.ValidationError)
	return value


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
