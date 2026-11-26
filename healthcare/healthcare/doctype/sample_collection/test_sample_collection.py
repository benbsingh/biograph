# Copyright (c) 2015, ESS and Contributors
# See license.txt


import frappe
from frappe.tests import IntegrationTestCase

from healthcare.healthcare.doctype.lab_test.test_lab_test import create_lab_test_sample
from healthcare.healthcare.doctype.patient_appointment.test_patient_appointment import (
	create_patient,
)


class TestSampleCollection(IntegrationTestCase):
	def test_sample_id_cannot_be_renamed(self):
		sample = frappe.get_doc(
			{
				"doctype": "Sample Collection",
				"patient": create_patient(),
				"sample": create_lab_test_sample(),
			}
		)
		sample.insert(ignore_permissions=True)
		self.assertRaises(frappe.ValidationError, frappe.rename_doc, "Sample Collection", sample.name, "SAMPLE-RENAMED")
		self.assertTrue(frappe.db.exists("Sample Collection", sample.name))
