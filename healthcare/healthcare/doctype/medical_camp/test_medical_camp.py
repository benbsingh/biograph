import frappe
from frappe.tests import IntegrationTestCase

from healthcare.healthcare.doctype.patient_appointment.test_patient_appointment import create_patient


class TestMedicalCamp(IntegrationTestCase):
	def test_camp_child_tables_and_links(self):
		camp = frappe.get_meta("Medical Camp")
		self.assertEqual(camp.get_field("participants").fieldtype, "Table")
		self.assertEqual(camp.get_field("participants").options, "Medical Camp Participant")
		self.assertEqual(camp.get_field("activities").fieldtype, "Table")
		self.assertEqual(camp.get_field("activities").options, "Medical Camp Activity")
		self.assertEqual(camp.get_field("organizer").options, "Healthcare Practitioner")

		participant = frappe.get_meta("Medical Camp Participant")
		self.assertTrue(participant.istable)
		patient = participant.get_field("patient")
		self.assertEqual(patient.fieldtype, "Link")
		self.assertEqual(patient.options, "Patient")
		self.assertTrue(patient.reqd)

		activity = frappe.get_meta("Medical Camp Activity")
		self.assertTrue(activity.istable)
		self.assertEqual(activity.get_field("responsible_practitioner").options, "Healthcare Practitioner")

	def test_camp_role_permissions_preserve_sharing(self):
		camp = frappe.get_meta("Medical Camp")
		self.assertFalse(camp.is_submittable)
		permissions = {perm.role: perm for perm in camp.permissions}
		self.assertIn("Physician", permissions)
		for right in ("read", "create", "write", "delete", "share"):
			self.assertTrue(permissions["Physician"].get(right), f"Physician must have {right}")

	def test_participant_and_activity_round_trip(self):
		patient = create_patient()
		camp = frappe.get_doc(
			{
				"doctype": "Medical Camp",
				"naming_series": "MC-.YYYY.-.#####",
				"camp_type": "General",
				"participants": [{"patient": patient, "visit_reason": "Screening"}],
				"activities": [{"activity": "Blood pressure check"}],
			}
		).insert()

		reopened = frappe.get_doc("Medical Camp", camp.name)
		self.assertEqual(len(reopened.participants), 1)
		self.assertEqual(reopened.participants[0].patient, patient)
		self.assertEqual(len(reopened.activities), 1)
		self.assertEqual(reopened.activities[0].activity, "Blood pressure check")
