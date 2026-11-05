"""Kiosk role and its create-only permissions.

Called from after_migrate, setup_healthcare and the setup_kiosk_role patch; every call is safe to repeat.
"""

import frappe
from frappe.permissions import add_permission, setup_custom_perms, update_permission_property

KIOSK_ROLE = "Kiosk"
KIOSK_DOCTYPES = ("Patient", "Patient Appointment")

_PERM_TYPES = (
	"read", "write", "delete", "submit", "cancel", "amend", "report", "export", "import", "share", "print", "email", "select",
)  # fmt: skip


def log_deferred(title, detail):
	"""Log to Error Log through Redis (it survives the request's rollback), without the request's arguments.

	frappe.log_error also stores sanitized_dict(frappe.form_dict) in Error Log.metadata, and that only masks keys such
	as password, token or key. A kiosk request's arguments (name, dob, mobile, aadhaar, otp, ABHA address) would be
	stored in clear text, so the form_dict is swapped for an empty one for the duration of the call. Callers pass a
	title and a class name, never patient data. A logging failure (Redis down) never replaces the caller's answer.
	"""
	saved = frappe.local.form_dict
	frappe.local.form_dict = frappe._dict()
	try:
		frappe.log_error(title=title, message=detail, defer_insert=True)
	except Exception:
		pass
	finally:
		frappe.local.form_dict = saved


def ensure_kiosk_role_and_permissions():
	# Every migrate resets the Kiosk rows to create-only on purpose: this is a security boundary, so a loosened
	# (or tightened) Custom DocPerm made by hand in Role Permission Manager does not survive.
	_ensure_role()
	for doctype in KIOSK_DOCTYPES:
		_ensure_create_only(doctype)
	_ensure_one_token_per_patient_per_day()


def _ensure_one_token_per_patient_per_day():
	"""Back the kiosk's registration lock with the database: a patient gets one token a day, however requests race."""
	if frappe.db.table_exists("Kiosk Token"):
		# A site that issued tokens before the index existed may hold two for one patient and day, and the ALTER
		# would fail on them (and so would every migrate). Keep the lowest-named token of each pair: names are
		# KT-<date>-<sequence>, so the lowest is the first one issued, which is deliberate. The removed ones are
		# counted in an Error Log entry (their rows also go to Deleted Document).
		extras = frappe.db.sql(
			"""select name from `tabKiosk Token` t where exists (
				select 1 from `tabKiosk Token` o
				where o.patient = t.patient and o.token_date = t.token_date and o.name < t.name)""",
			pluck="name",
		)
		for name in extras:
			frappe.delete_doc("Kiosk Token", name, ignore_permissions=True, force=True)
		if extras:
			log_deferred("Kiosk Token duplicates removed", f"{len(extras)} duplicate token(s)")
		frappe.db.add_unique("Kiosk Token", ["patient", "token_date"], constraint_name="unique_kiosk_token_patient_day")


def _ensure_role():
	if frappe.db.exists("Role", KIOSK_ROLE):
		return
	frappe.get_doc({"doctype": "Role", "role_name": KIOSK_ROLE, "desk_access": 0}).insert(ignore_permissions=True)


def _ensure_create_only(doctype):
	# Never insert a bare Custom DocPerm: once one exists Frappe ignores all the standard DocPerm rows
	# for the doctype. setup_custom_perms copies the standard rows across first, so other roles keep access.
	#
	# Trade-off: from here on this site's Patient and Patient Appointment permissions live in Custom DocPerm,
	# so a permission change made later in the app's patient.json / patient_appointment.json is not applied on a
	# migrated site (fresh installs still get it). Change those permissions in Custom DocPerm (Role Permissions
	# Manager) or in a patch that edits it, not only in the JSON.
	setup_custom_perms(doctype)
	filters = {"parent": doctype, "role": KIOSK_ROLE, "permlevel": 0, "if_owner": 0}
	if not frappe.db.exists("Custom DocPerm", filters):
		add_permission(doctype, KIOSK_ROLE, 0, "create")

	row = frappe.db.get_value("Custom DocPerm", filters, ["create", *_PERM_TYPES], as_dict=True)
	for ptype in _PERM_TYPES:
		if row.get(ptype):
			update_permission_property(doctype, KIOSK_ROLE, 0, ptype, 0, validate=False)
	if not row.create:
		update_permission_property(doctype, KIOSK_ROLE, 0, "create", 1, validate=False)
