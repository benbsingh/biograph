import frappe
from frappe import _

# Roles that may run each write-type ptype on a lab Observation (Q-2 = B).
# Ptypes not listed (read, print) are left to the DocType grants.
LAB_OBSERVATION_ROLES = {
	"create": {"Laboratory User", "System Manager"},
	"write": {"Laboratory User", "LabTest Approver", "System Manager"},
	"submit": {"LabTest Approver", "System Manager"},
	"share": set(),  # the matrix grants no share on Observation to any role
	"cancel": {"System Manager"},
	"amend": {"System Manager"},
	"delete": {"System Manager"},
}

# Frappe grants these ptypes through a DocShare row after the controller has denied them
# (frappe.permissions.has_permission runs false_if_not_shared once the role check fails).
# A plain False from the hook would therefore not stop a shared record, so for these ptypes the
# hook raises when a DocShare row for that right exists on the document.
SHARE_BYPASSABLE_PTYPES = {"write", "submit", "share"}


def observation_has_permission(doc, ptype, user=None):
	"""Deny write-type ptypes on lab Observations for roles outside the matrix.

	Frappe treats any falsy controller return as a deny, so "no objection" must
	be True, not None. True does not grant: role grants are checked after the hook.
	Vitals and other categories return True and keep their DocType grants.
	A DocShare write, submit or share right on a lab Observation does not override the
	matrix: the hook raises PermissionError for those ptypes when the matrix refuses them.
	"""
	if doc.get("observation_category") != "Laboratory":
		return True
	allowed_roles = LAB_OBSERVATION_ROLES.get(ptype)
	if allowed_roles is None:
		return True
	user = user or frappe.session.user
	if set(frappe.get_roles(user)) & allowed_roles:
		return True
	if ptype in SHARE_BYPASSABLE_PTYPES and frappe.share.get_shared(
		"Observation", user, rights=[ptype], filters=[["share_name", "=", doc.name]], limit=1
	):
		frappe.throw(
			_("You do not have permission to {0} this lab observation.").format(ptype),
			frappe.PermissionError,
		)
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
