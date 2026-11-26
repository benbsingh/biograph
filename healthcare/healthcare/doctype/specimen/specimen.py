# Copyright (c) 2023, healthcare and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from healthcare.healthcare.doctype.lims_audit_entry.lims_audit_entry import record_audit_entry

ACCESSION_STATUSES = ("Accepted", "Rejected")
ACCESSION_DECISIONS = {"Accept": "Accepted", "Reject": "Rejected"}


class Specimen(Document):
	def before_insert(self):
		patient_doc = frappe.get_doc("Patient", self.patient)
		if patient_doc.dob:
			self.patient_age = patient_doc.calculate_age().get("age_in_string")

	def after_insert(self):
		self.db_set("barcode", self.name)

	def validate(self):
		if self.is_new():
			return

		if self.has_value_changed("accession_status") and not self.flags.accession_action:
			frappe.throw(
				_("Accession Status can only change through accept or reject, not by editing the Specimen."),
				title=_("Not Allowed"),
			)

		if self.accession_status == "Rejected" and not (self.rejection_reason or "").strip():
			frappe.throw(_("A rejection reason is required to reject a specimen."), title=_("Missing Reason"))


@frappe.whitelist()
def accession_specimen(specimen, decision, reason=None):
	"""Accept or reject a specimen once. Every decision writes a LIMS Audit Entry in the same transaction.

	Reversing a decision needs a second person and is not supported here.
	"""
	if decision not in ACCESSION_DECISIONS:
		frappe.throw(_("Decision must be Accept or Reject."))

	doc = frappe.get_doc("Specimen", specimen)
	doc.check_permission("write")

	if doc.accession_status in ACCESSION_STATUSES:
		frappe.throw(
			_(
				"Specimen {0} is already {1}. Reversing a decision needs a second person and is not supported."
			).format(doc.name, doc.accession_status),
			title=_("Already Accessioned"),
		)

	reason = (reason or "").strip()
	if decision == "Reject" and not reason:
		frappe.throw(_("A rejection reason is required to reject a specimen."), title=_("Missing Reason"))

	if not doc.lab_test:
		frappe.throw(
			_("Link a Lab Test to the specimen before it is accepted or rejected."), title=_("Not Routed")
		)

	previous_status = doc.accession_status or "Pending"
	new_status = ACCESSION_DECISIONS[decision]

	doc.accession_status = new_status
	doc.accessioned_by = frappe.session.user
	doc.accessioned_at = now_datetime()
	if decision == "Reject":
		doc.rejection_reason = reason
	doc.flags.accession_action = True
	doc.save()

	record_audit_entry(
		action=f"Specimen {new_status}",
		reference_doctype="Specimen",
		reference_name=doc.name,
		reason=reason or None,
		details={
			"from_status": previous_status,
			"to_status": new_status,
			"lab_test": doc.lab_test,
			"department": doc.department,
		},
	)

	return {"name": doc.name, "accession_status": doc.accession_status, "department": doc.department}
