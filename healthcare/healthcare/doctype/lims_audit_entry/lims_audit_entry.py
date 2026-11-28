# Copyright (c) 2026, healthcare and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


class LIMSAuditEntry(Document):
	"""Append-only activity log (BR-1.4). A row is written once and never edited or deleted."""

	def validate(self):
		if not self.is_new():
			frappe.throw(
				_("LIMS Audit Entry rows are append-only and cannot be changed."), frappe.PermissionError
			)

	def on_trash(self):
		frappe.throw(_("LIMS Audit Entry rows cannot be deleted."), frappe.PermissionError)


def record_audit_entry(action, reference_doctype, reference_name, reason=None, details=None):
	"""Write one audit row for the current user. Call it inside the transaction that makes the change."""
	entry = frappe.get_doc(
		{
			"doctype": "LIMS Audit Entry",
			"occurred_at": now_datetime(),
			"user": frappe.session.user,
			"action": action,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"reason": reason,
			"details": frappe.as_json(details, indent=None) if details else None,
		}
	)
	entry.insert(ignore_permissions=True)
	return entry.name
