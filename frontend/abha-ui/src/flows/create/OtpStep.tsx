import { useState } from "react";
import { completeEnrolment, requestEnrolmentOtp, resendEnrolmentOtp } from "../../api/abha";
import { useKnown } from "../../domain/StoreContext";
import { Field } from "../../ui/Field";
import { OtpPanel } from "../../ui/OtpPanel";
import { digitsOnly, mobileError } from "../../ui/validate";
import type { EnrolResult } from "../../api/types";

/**
 * Verify the Aadhaar OTP and create the ABHA. When the Aadhaar number was already given elsewhere (a lookup that found
 * no ABHA), the OTP has not been requested yet and this screen requests it, so the number is never asked for twice.
 *
 * Enrolment requires a mobile number (ABDM's documented contract). If the Patient has no usable mobile on record, this
 * step collects one before verifying the OTP so the call can succeed; otherwise the saved number is reused silently.
 */
export function OtpStep({ onNext, onBack, hasMobile }: { onNext: (result: { mobileLinked: boolean; isNew: boolean }) => void; onBack: () => void; hasMobile: boolean }) {
  const { known, dispatch } = useKnown();
  const patient = known.patient?.name;
  const needsSend = !known.txnId;
  const [mobile, setMobile] = useState("");
  const [touched, setTouched] = useState(false);
  const mobileProblem = hasMobile ? null : mobileError(mobile);

  async function firstSend() {
    if (!patient || !known.aadhaar || !known.consent) throw new Error("The Aadhaar number or the agreement is missing. Go back and enter it.");
    // Snapshot the patient/consent that started this request. If staff switch patients
    // while the OTP request is in flight, the old step must not merge its result onto the
    // new patient's state.
    const startedPatient = patient;
    const startedConsentId = known.consent.id;
    const res = await requestEnrolmentOtp(startedPatient, known.aadhaar, startedConsentId);
    if (known.patient?.name !== startedPatient || known.consent?.id !== startedConsentId) return;
    dispatch({ type: "learn", patch: { txnId: res.txnId } });
  }

  async function resend() {
    if (!patient || !known.txnId) throw new Error("The OTP request has expired. Go back and send it again.");
    await resendEnrolmentOtp(patient, known.txnId);
  }

  async function verify(code: string) {
    if (!patient || !known.txnId) throw new Error("The OTP request has expired. Go back and send it again.");
    if (!hasMobile) {
      setTouched(true);
      if (mobileProblem) throw new Error(mobileProblem);
    }
    // Snapshot the patient that started verification; reject the result if the patient changed.
    const startedPatient = patient;
    const chosenMobile = hasMobile ? undefined : digitsOnly(mobile);
    const res = await completeEnrolment(startedPatient, known.txnId, code, chosenMobile);
    if (known.patient?.name !== startedPatient) return;
    const enrolRes = res as EnrolResult;
    dispatch({ type: "learn", patch: { txnId: res.txnId ?? known.txnId, abhaNumber: res.abha_number, mobileLinked: res.mobile_linked, isNew: enrolRes.is_new } });
    dispatch({ type: "forget", keys: ["aadhaar"] });
    onNext({ mobileLinked: res.mobile_linked, isNew: enrolRes.is_new });
  }

  return (
    <OtpPanel
      heading="Verify Aadhaar OTP"
      lede={
        hasMobile
          ? "Enter the 6-digit OTP sent to the mobile number linked to the Aadhaar. It is checked as soon as the sixth digit is in."
          : "Enter the patient's mobile number and the 6-digit OTP sent to the mobile number linked to the Aadhaar. The mobile number is required to complete enrolment."
      }
      label="Aadhaar OTP"
      verify={verify}
      resend={resend}
      send={needsSend ? firstSend : undefined}
      back={{ label: "Change Aadhaar number", onClick: onBack }}
    >
      {!hasMobile && (
        <Field
          label="Mobile number"
          inputMode="numeric"
          autoComplete="off"
          maxLength={14}
          placeholder="10 digits"
          value={mobile}
          onChange={(e) => setMobile(e.target.value)}
          onBlur={() => setTouched(true)}
          error={touched ? mobileProblem : null}
          hint="10 digits (required for enrolment)"
        />
      )}
    </OtpPanel>
  );
}
