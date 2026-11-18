import { requestDocumentOtp, verifyDocumentOtp } from "../../api/abha";
import { useKnown } from "../../domain/StoreContext";
import { OtpPanel } from "../../ui/OtpPanel";

export function DlOtpStep({ onNext, onBack }: { onNext: () => void; onBack: () => void }) {
  const { known, dispatch } = useKnown();
  const patient = known.patient?.name;

  async function send() {
    if (!patient || !known.consent) throw new Error("The agreement is missing. Go back and record it.");
    // Snapshot the patient/consent that started this OTP request. If staff switch patients
    // while the request is in flight, the old step must not merge its result onto the new
    // patient's state.
    const startedPatient = patient;
    const startedConsentId = known.consent.id;
    const res = await requestDocumentOtp(startedPatient, startedConsentId, known.communicationMobile);
    if (known.patient?.name !== startedPatient || known.consent?.id !== startedConsentId) return;
    dispatch({ type: "learn", patch: { txnId: res.txnId } });
  }

  async function verify(code: string) {
    if (!patient || !known.txnId) throw new Error("The OTP request has expired. Go back and send it again.");
    // Snapshot the patient that started verification; reject the result if the patient changed.
    const startedPatient = patient;
    const res = await verifyDocumentOtp(startedPatient, known.txnId, code);
    if (known.patient?.name !== startedPatient) return;
    dispatch({ type: "learn", patch: { txnId: res.txnId ?? known.txnId } });
    dispatch({ type: "forget", keys: ["communicationMobile"] });
    onNext();
  }

  return (
    <OtpPanel
      heading="Verify mobile OTP"
      lede="Enter the 6-digit OTP sent to the mobile number. It is checked as soon as the sixth digit is in."
      label="Mobile OTP"
      verify={verify}
      resend={send}
      send={send}
      back={{ label: "Change the number", onClick: onBack }}
    />
  );
}
