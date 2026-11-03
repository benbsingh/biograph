# Copyright (c) 2015, ESS LLP and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.model.document import Document


class MedicalDepartment(Document):
	def before_insert(self):
		self.validate()

	def validate(self):
		self.department = (self.department or "").strip()
		if not self.department:
			frappe.throw(_("Department cannot be blank"))

	def _sync_autoname_field(self):
		if self.is_new():
			super()._sync_autoname_field()
