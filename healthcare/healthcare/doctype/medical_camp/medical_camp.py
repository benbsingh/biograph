# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate


class MedicalCamp(Document):
	def before_validate(self):
		self.clean_specialities()

	def clean_specialities(self):
		"""Drop blank rows and default a row's department from its practitioner."""
		for row in list(self.specialities):
			if row.practitioner and not row.medical_department:
				row.medical_department = frappe.db.get_value(
					"Healthcare Practitioner", row.practitioner, "department"
				)
			if not row.medical_department and not row.practitioner:
				self.remove(row)

	def validate(self):
		self.validate_dates()
		self.validate_specialities()
		self.validate_appointment_type()

	def validate_dates(self):
		if getdate(self.end_date) < getdate(self.start_date):
			frappe.throw(_("End Date cannot be before Start Date"))

	def validate_specialities(self):
		if not self.specialities:
			frappe.throw(_("Please add at least one Speciality to the camp"))

		seen = set()
		for row in self.specialities:
			if row.medical_department in seen:
				frappe.throw(
					_("Row #{0}: Speciality {1} is added more than once").format(
						row.idx, frappe.bold(row.medical_department)
					)
				)
			seen.add(row.medical_department)

			if row.practitioner:
				department = frappe.db.get_value("Healthcare Practitioner", row.practitioner, "department")
				if department and department != row.medical_department:
					frappe.throw(
						_("Row #{0}: Practitioner {1} belongs to {2}, not {3}").format(
							row.idx,
							frappe.bold(row.practitioner),
							frappe.bold(department),
							frappe.bold(row.medical_department),
						)
					)

	def validate_appointment_type(self):
		if not self.appointment_type:
			return

		if frappe.db.get_value("Appointment Type", self.appointment_type, "allow_booking_for") != "Medical Camp":
			frappe.throw(
				_("Appointment Type {0} must have <b>Allow Booking For</b> set to Medical Camp").format(
					frappe.bold(self.appointment_type)
				)
			)
