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


def ensure_kiosk_role_and_permissions():
	_ensure_role()
	for doctype in KIOSK_DOCTYPES:
		_ensure_create_only(doctype)
	_ensure_one_token_per_patient_per_day()


def _ensure_one_token_per_patient_per_day():
	"""Back the kiosk's registration lock with the database: a patient gets one token a day, however requests race."""
	if frappe.db.table_exists("Kiosk Token"):
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
