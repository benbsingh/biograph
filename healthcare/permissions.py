import frappe
from frappe import _

# Roles that may run each write-type ptype on a lab Observation (Q-2 = B).
# Ptypes not listed (read, print) are left to the DocType grants.
LAB_OBSERVATION_ROLES = {
	"create": {"Laboratory User", "System Manager"},
	"write": {"Laboratory User", "LabTest Approver", "System Manager"},
	"submit": {"LabTest Approver", "System Manager"},
	"cancel": {"System Manager"},
	"amend": {"System Manager"},
	"delete": {"System Manager"},
}


def observation_has_permission(doc, ptype, user=None):
	"""Deny write-type ptypes on lab Observations for roles outside the matrix.

	Vitals and other categories return None and keep their DocType grants.
	Frappe reads a False from a controller hook as a deny. A None return
	defers to the grants. This hook never grants, so a role without a DocType
	grant stays refused.
	"""
	if doc.get("observation_category") != "Laboratory":
		return None
	allowed_roles = LAB_OBSERVATION_ROLES.get(ptype)
	if allowed_roles is None:
		return None
	user_roles = set(frappe.get_roles(user or frappe.session.user))
	if user_roles & allowed_roles:
		return None
	return False


def check_permission(doc, ptype, user):
	if ptype != "delete":
		return None
	if "System Manager" in frappe.get_roles(user):
		return None
	frappe.throw(
		_("You do not have permission to delete records."),
		frappe.PermissionError,
	)
