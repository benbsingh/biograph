# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

# The camp link is only checked for being filled, not for existing; skip building Medical Camp test records
IGNORE_TEST_RECORD_DEPENDENCIES = ["Medical Camp"]


class TestKioskSettings(IntegrationTestCase):
	def settings(self, **kwargs):
		doc = frappe.get_doc("Kiosk Settings")
		doc.update({"patient_source": "Walk-in", "medical_camp": None, "idle_timeout": 60, **kwargs})
		return doc

	def test_camp_mandatory_for_medical_camp_source(self):
		doc = self.settings(patient_source="Medical Camp")
		self.assertRaises(frappe.MandatoryError, doc.save)

	def test_idle_timeout_range(self):
		for value in (14, 601):
			self.assertRaises(frappe.ValidationError, self.settings(idle_timeout=value).save)

	def test_idle_timeout_defaults(self):
		doc = self.settings(idle_timeout=0)
		doc.save()
		self.assertEqual(doc.idle_timeout, 60)

	def test_camp_cleared_and_booking_off_for_other_sources(self):
		doc = self.settings(enable_appointment=1)
		doc.save()
		self.assertFalse(doc.medical_camp)
		self.assertFalse(doc.enable_appointment)

	def test_only_admin_roles_have_permissions(self):
		roles = {p.role for p in frappe.get_meta("Kiosk Settings").permissions}
		self.assertEqual(roles, {"System Manager", "Healthcare Administrator"})
