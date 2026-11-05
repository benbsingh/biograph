# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

MIN_IDLE_TIMEOUT = 15
MAX_IDLE_TIMEOUT = 600
DEFAULT_IDLE_TIMEOUT = 60


class KioskSettings(Document):
	def validate(self):
		if not self.idle_timeout:
			self.idle_timeout = DEFAULT_IDLE_TIMEOUT
		if not MIN_IDLE_TIMEOUT <= self.idle_timeout <= MAX_IDLE_TIMEOUT:
			frappe.throw(
				_("Idle Timeout must be between {0} and {1} seconds").format(MIN_IDLE_TIMEOUT, MAX_IDLE_TIMEOUT)
			)

		if self.patient_source == "Medical Camp" and not self.medical_camp:
			frappe.throw(_("Camp is mandatory when Patient Source is Medical Camp"), frappe.MandatoryError)

		if self.patient_source != "Medical Camp":
			self.medical_camp = None
			# Booking is camp-bound: a camp supplies the appointment type
			self.enable_appointment = 0
